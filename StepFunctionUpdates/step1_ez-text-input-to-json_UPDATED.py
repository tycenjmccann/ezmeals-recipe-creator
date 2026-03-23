import json
import boto3
import logging
import random
from botocore.config import Config
from botocore.exceptions import ClientError
import time
from typing import Dict, Any, List
import uuid

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configure the Boto3 client with extended timeout and retry settings
config = Config(
    region_name="us-west-2",
    connect_timeout=120,
    read_timeout=180,
    retries={
        'max_attempts': 3,
        'mode': 'adaptive'
    }
)

# Create a Bedrock Runtime client with the updated config
client = boto3.client("bedrock-runtime", config=config)

# Ordered profile IDs (cross-Region pool) - NEW MODEL FALLBACK SYSTEM
MODEL_PROFILES = [
    "us.anthropic.claude-opus-4-6-v1",              # Claude 4.6 Opus - highest quality
    "us.anthropic.claude-sonnet-4-20250514-v1:0",   # Claude 4 Sonnet - good balance, ~65K context
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0"  # Claude 3.7 Sonnet - reliable fallback
]

def invoke_claude(messages: list, cfg: dict):
    """Try each profile until one succeeds; bubble up other errors."""
    for pid in MODEL_PROFILES:
        try:
            logger.info(f"Attempting to use model profile: {pid}")
            return client.converse(
                modelId=pid,
                messages=messages,
                inferenceConfig=cfg
            )
        except client.exceptions.AccessDeniedException:
            # profile exists but model not enabled in this account/Region
            logger.warning(f"Access denied for model profile {pid}, trying next...")
            continue
        except client.exceptions.ThrottlingException:
            # Region-local capacity full; profile will auto-route,
            # but keep trying next profile if all Regions saturate
            logger.warning(f"Throttling for model profile {pid}, trying next...")
            continue
        except Exception as e:
            logger.warning(f"Error with model profile {pid}: {str(e)}, trying next...")
            continue
    raise RuntimeError("No Claude profile is currently available")

# Schema validation constants - REMOVED processingNotes from recipe JSON schema
REQUIRED_FIELDS = [
    "id",
    "title", 
    "dishType",
    "primary",
    "baseMainId",
    "imageURL",
    "imageThumbURL", 
    "description",
    "link",
    "prepTime",
    "cookTime",
    "rating",
    "servings",
    "cuisineType",
    "isQuick",
    "isBalanced", 
    "isGourmet",
    "ingredients",
    "ingredient_objects",
    "instructions",
    "notes",
    "recommendedSides",
    "includedSides",
    "comboIndex",
    "products",
    "glutenFree",
    "vegetarian",
    "slowCook",
    "instaPot",
    "flagged"
    # processingNotes REMOVED - will be at stepOutput level
]

CUISINE_TYPES = [
    "Global Cuisines", "American", "Asian", "Indian", "Italian", 
    "Latin", "Soups & Stews"
]

DISH_TYPES = ["main", "side"]

def validate_input(recipe_text: str) -> bool:
    """
    Validate the input recipe text.
    """
    if not recipe_text or len(recipe_text.strip()) < 10:
        raise ValueError("Recipe text is too short or empty")
    if len(recipe_text) > 10000:
        raise ValueError("Recipe text exceeds maximum length")
    return True

def validate_and_fix_response_schema(response_json: Dict[str, Any]) -> List[str]:
    """
    Validate the response JSON against required schema and auto-fix common issues.
    Returns list of processing notes instead of adding them to JSON.
    """
    processing_notes = []
    
    # Check for all required fields first
    missing_fields = [field for field in REQUIRED_FIELDS if field not in response_json]
    if missing_fields:
        # Add missing fields with defaults
        for field in missing_fields:
            if field in ["recommendedSides", "includedSides", "products", "notes"]:
                response_json[field] = {"L": []}
            elif field == "comboIndex":
                response_json[field] = {"M": {}}
            elif field == "ingredient_objects":
                response_json[field] = {"L": []}
            elif field == "ingredients":
                response_json[field] = {"L": []}
            elif field == "instructions":
                response_json[field] = {"L": []}
            elif field in ["glutenFree", "vegetarian", "slowCook", "instaPot", "flagged", "primary"]:
                response_json[field] = {"BOOL": False}
            elif field in ["isQuick", "isBalanced", "isGourmet"]:
                response_json[field] = {"BOOL": False}
            elif field in ["prepTime", "cookTime", "rating", "servings"]:
                response_json[field] = {"N": "0"}
            elif field in ["title", "dishType", "baseMainId", "imageURL", "imageThumbURL", "description", "link", "cuisineType"]:
                response_json[field] = {"S": ""}
            elif field == "id":
                response_json[field] = {"S": str(uuid.uuid4())}
        
        processing_notes.append(f"Added missing fields: {', '.join(missing_fields)}")
    
    # Auto-fix cooking time logic
    try:
        prep_time = int(response_json.get("prepTime", {}).get("N", "0"))
        cook_time = int(response_json.get("cookTime", {}).get("N", "0"))
        total_time = prep_time + cook_time
        
        is_quick = response_json.get("isQuick", {}).get("BOOL", False)
        is_balanced = response_json.get("isBalanced", {}).get("BOOL", False)
        is_gourmet = response_json.get("isGourmet", {}).get("BOOL", False)
        
        # Fix cooking time flags based on total time
        if total_time <= 30:
            if not is_quick or is_balanced or is_gourmet:
                response_json["isQuick"] = {"BOOL": True}
                response_json["isBalanced"] = {"BOOL": False}
                response_json["isGourmet"] = {"BOOL": False}
                processing_notes.append(f"Fixed isQuick flag: {total_time} min total, changed to isQuick=true")
        elif 30 < total_time <= 60:
            if is_quick or not is_balanced or is_gourmet:
                response_json["isQuick"] = {"BOOL": False}
                response_json["isBalanced"] = {"BOOL": True}
                response_json["isGourmet"] = {"BOOL": False}
                processing_notes.append(f"Fixed isBalanced flag: {total_time} min total, changed to isBalanced=true")
        else:  # > 60 minutes
            if is_quick or is_balanced or not is_gourmet:
                response_json["isQuick"] = {"BOOL": False}
                response_json["isBalanced"] = {"BOOL": False}
                response_json["isGourmet"] = {"BOOL": True}
                processing_notes.append(f"Fixed isGourmet flag: {total_time} min total, changed to isGourmet=true")
    except (ValueError, KeyError):
        processing_notes.append("Could not validate cooking time flags - invalid time values")
    
    # Auto-fix image URL format
    image_url = response_json.get("imageURL", {}).get("S", "")
    if image_url and not image_url.startswith("menu-item-images/"):
        if not image_url.startswith("http"):
            response_json["imageURL"]["S"] = f"menu-item-images/{image_url}"
            processing_notes.append(f"Fixed imageURL format: added menu-item-images/ prefix")
    
    # Auto-fix thumbnail URL format
    thumb_url = response_json.get("imageThumbURL", {}).get("S", "")
    if thumb_url and not thumb_url.startswith("menu-item-images/"):
        if not thumb_url.startswith("http"):
            response_json["imageThumbURL"]["S"] = f"menu-item-images/{thumb_url}"
            processing_notes.append(f"Fixed imageThumbURL format: added menu-item-images/ prefix")
    
    # Validate cuisine type
    cuisine_type = response_json.get("cuisineType", {}).get("S", "")
    if cuisine_type not in CUISINE_TYPES:
        response_json["cuisineType"]["S"] = "Global Cuisines"
        processing_notes.append(f"Fixed invalid cuisine type '{cuisine_type}' to 'Global Cuisines'")
    
    # Validate dish type
    dish_type = response_json.get("dishType", {}).get("S", "")
    if dish_type not in DISH_TYPES:
        response_json["dishType"]["S"] = "main"
        processing_notes.append(f"Fixed invalid dish type '{dish_type}' to 'main'")
    
    return processing_notes

def call_claude_for_recipe_conversion(prompt: str, request_id: str) -> Dict[str, Any]:
    """
    Call Claude to convert recipe text to JSON format using the new converse API with model fallback.
    """
    
    # Prepare the conversation for the new converse API
    conversation = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }
    ]
    
    # Use the new invoke_claude function with model fallback
    response = invoke_claude(
        conversation,
        {"maxTokens": 4096, "temperature": 1}
    )
    
    # Extract the response text from the new API format
    response_text = response['output']['message']['content'][0]['text']
    
    # Log the raw response from Claude
    logger.info(f"Raw response from Claude: {response_text}")

    # Now attempt to parse the JSON
    try:
        # Try to find JSON in the response
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError("No JSON found in Claude's response")
        
        json_str = response_text[json_start:json_end]
        response_json = json.loads(json_str)
        
        logger.info(f"Request {request_id} - Successfully parsed JSON from Claude")
        return response_json
        
    except json.JSONDecodeError as e:
        logger.error(f"Request {request_id} - JSON parsing error: {e}")
        logger.error(f"Request {request_id} - Raw response: {response_text}")
        raise ValueError(f"Invalid JSON response from Claude: {e}")
    except Exception as e:
        logger.error(f"Request {request_id} - Unexpected error parsing response: {e}")
        raise

def lambda_handler(event, context):
    """
    AWS Lambda handler for converting recipe text to structured JSON.
    """
    request_id = str(uuid.uuid4())
    logger.info(f"Request {request_id} - Starting recipe text to JSON conversion")
    
    try:
        # Extract recipe text from the event
        recipe_text = event.get('recipe', '')
        
        if not recipe_text:
            logger.error(f"Request {request_id} - No recipe text provided")
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'No recipe text provided'}),
                'processingNotes': [f"Step 1: Error - No recipe text provided"]
            }
        
        # Validate input
        validate_input(recipe_text)
        logger.info(f"Request {request_id} - Input validation passed")
        
        # Create the prompt for Claude
        prompt = f"""
Convert this recipe text into a structured JSON format that matches the DynamoDB schema. The JSON should be ready for direct insertion into DynamoDB.

Recipe Text:
{recipe_text}

Requirements:
1. Generate a unique UUID for the "id" field
2. Set "primary" to true for main dishes, false for sides
3. Set "baseMainId" to the same value as "id" for main dishes
4. Use proper DynamoDB attribute types (S for strings, N for numbers, BOOL for booleans, L for lists, M for maps)
5. Ensure all cooking time flags (isQuick, isBalanced, isGourmet) are mutually exclusive
6. Set isQuick=true for recipes ≤30 min total time, isBalanced=true for 31-60 min, isGourmet=true for >60 min
7. Include proper image URLs in format "menu-item-images/recipe-name.jpg"
8. Categorize cuisine type as one of: {', '.join(CUISINE_TYPES)}
9. Set dishType to either "main" or "side"
10. Include all ingredients as a list of strings in the "ingredients" field
11. Include step-by-step instructions as a list of strings in the "instructions" field
12. Initialize empty arrays for: recommendedSides, includedSides, products, notes
13. Initialize empty object for: comboIndex, ingredient_objects
14. Set appropriate boolean flags for dietary restrictions and cooking methods

Return ONLY the JSON object, no additional text or explanation.

Example structure:
{{
    "id": {{"S": "uuid-here"}},
    "title": {{"S": "Recipe Name"}},
    "dishType": {{"S": "main"}},
    "primary": {{"BOOL": true}},
    "baseMainId": {{"S": "same-as-id"}},
    "imageURL": {{"S": "menu-item-images/recipe-name.jpg"}},
    "imageThumbURL": {{"S": "menu-item-images/recipe-name-thumb.jpg"}},
    "description": {{"S": "Brief description"}},
    "link": {{"S": ""}},
    "prepTime": {{"N": "15"}},
    "cookTime": {{"N": "30"}},
    "rating": {{"N": "4"}},
    "servings": {{"N": "4"}},
    "cuisineType": {{"S": "Italian"}},
    "isQuick": {{"BOOL": false}},
    "isBalanced": {{"BOOL": true}},
    "isGourmet": {{"BOOL": false}},
    "ingredients": {{"L": [{{"S": "ingredient 1"}}, {{"S": "ingredient 2"}}]}},
    "ingredient_objects": {{"L": []}},
    "instructions": {{"L": [{{"S": "Step 1"}}, {{"S": "Step 2"}}]}},
    "notes": {{"L": []}},
    "recommendedSides": {{"L": []}},
    "includedSides": {{"L": []}},
    "comboIndex": {{"M": {{}}}},
    "products": {{"L": []}},
    "glutenFree": {{"BOOL": false}},
    "vegetarian": {{"BOOL": false}},
    "slowCook": {{"BOOL": false}},
    "instaPot": {{"BOOL": false}},
    "flagged": {{"BOOL": false}}
}}
"""
        
        # Call Claude to convert the recipe
        logger.info(f"Request {request_id} - Calling Claude for recipe conversion")
        response_json = call_claude_for_recipe_conversion(prompt, request_id)
        
        # Validate and auto-fix the response
        logger.info(f"Request {request_id} - Validating and fixing response schema")
        processing_notes = validate_and_fix_response_schema(response_json)
        
        # Add step identifier to processing notes
        step_notes = []
        if processing_notes:
            for note in processing_notes:
                step_notes.append(f"Step 1: {note}")
        else:
            step_notes.append("Step 1: Recipe converted to JSON successfully")
        
        logger.info(f"Request {request_id} - Recipe conversion completed successfully")
        
        return {
            'statusCode': 200,
            'body': json.dumps(response_json),
            'processingNotes': step_notes
        }
        
    except ValueError as e:
        logger.error(f"Request {request_id} - Validation error: {e}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': str(e)}),
            'processingNotes': [f"Step 1: Validation error - {str(e)}"]
        }
    except ClientError as e:
        logger.error(f"Request {request_id} - Bedrock client error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Bedrock service error: {str(e)}'}),
            'processingNotes': [f"Step 1: Bedrock service error - {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Request {request_id} - Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'}),
            'processingNotes': [f"Step 1: Internal server error - {str(e)}"]
        }
