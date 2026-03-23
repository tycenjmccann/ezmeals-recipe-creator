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

# Model ID for Claude 3 Opus
model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"

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
            elif field in ["baseMainId", "link"]:
                response_json[field] = {"S": ""}
            else:
                processing_notes.append(f"Warning: Missing required field '{field}' - added default value")
    
    # Validate and fix specific field types and constraints
    try:
        # String fields that must not be empty
        required_string_fields = ["id", "title", "description", "cuisineType", "imageURL", "imageThumbURL", "servings"]
        for field in required_string_fields:
            if not isinstance(response_json[field].get("S"), str):
                processing_notes.append(f"Error: {field} must be a string")
                continue
            if not response_json[field]["S"].strip():
                processing_notes.append(f"Error: {field} cannot be empty")
                continue

        # String fields that can be empty (link, baseMainId)
        optional_string_fields = ["link", "baseMainId"]
        for field in optional_string_fields:
            if not isinstance(response_json[field].get("S"), str):
                processing_notes.append(f"Error: {field} must be a string")

        # Numeric fields
        numeric_fields = ["prepTime", "cookTime", "rating"]
        for field in numeric_fields:
            if not isinstance(response_json[field].get("N"), str):
                processing_notes.append(f"Error: {field} must be a number")
                continue
            if not response_json[field]["N"].isdigit():
                processing_notes.append(f"Error: {field} must be a valid number")
                continue

        # Boolean fields
        boolean_fields = [
            "primary", "isQuick", "isBalanced", "isGourmet", "glutenFree",
            "vegetarian", "slowCook", "instaPot", "flagged"
        ]
        for field in boolean_fields:
            if not isinstance(response_json[field].get("BOOL"), bool):
                processing_notes.append(f"Error: {field} must be a boolean")

        # Required non-empty list fields
        required_list_fields = ["ingredients", "instructions"]
        for field in required_list_fields:
            if not isinstance(response_json[field].get("L"), list):
                processing_notes.append(f"Error: {field} must be a list")
                continue
            if not response_json[field]["L"]:
                processing_notes.append(f"Warning: {field} is empty")

        # Optional list fields that can be empty
        optional_list_fields = [
            "ingredient_objects", "notes", "recommendedSides", "includedSides", "products"
        ]
        for field in optional_list_fields:
            if not isinstance(response_json[field].get("L"), list):
                processing_notes.append(f"Error: {field} must be a list")

        # Validate dishType
        if response_json['dishType']["S"] not in DISH_TYPES:
            old_value = response_json['dishType']["S"]
            # Auto-fix: default to "main" if invalid
            response_json['dishType']["S"] = "main"
            processing_notes.append(f"Fixed dishType: '{old_value}' is invalid, changed to 'main'")

        # Cuisine type validation and auto-fix
        if response_json['cuisineType']["S"] not in CUISINE_TYPES:
            old_value = response_json['cuisineType']["S"]
            # Auto-fix: default to "Global Cuisines" if invalid
            response_json['cuisineType']["S"] = "Global Cuisines"
            processing_notes.append(f"Fixed cuisineType: '{old_value}' is invalid, changed to 'Global Cuisines'")

        # AUTO-FIX: Cooking time logic (the main issue you encountered)
        total_time = int(response_json['prepTime']["N"]) + int(response_json['cookTime']["N"])
        
        # Check and fix isQuick
        correct_quick = (0 <= total_time <= 30)
        if response_json['isQuick']["BOOL"] != correct_quick:
            old_value = response_json['isQuick']["BOOL"]
            response_json['isQuick']["BOOL"] = correct_quick
            processing_notes.append(f"Fixed isQuick flag: {total_time} min total, changed from {old_value} to {correct_quick}")
        
        # Check and fix isBalanced
        correct_balanced = (35 <= total_time <= 60)
        if response_json['isBalanced']["BOOL"] != correct_balanced:
            old_value = response_json['isBalanced']["BOOL"]
            response_json['isBalanced']["BOOL"] = correct_balanced
            processing_notes.append(f"Fixed isBalanced flag: {total_time} min total, changed from {old_value} to {correct_balanced}")
        
        # Check and fix isGourmet
        correct_gourmet = (total_time > 60)
        if response_json['isGourmet']["BOOL"] != correct_gourmet:
            old_value = response_json['isGourmet']["BOOL"]
            response_json['isGourmet']["BOOL"] = correct_gourmet
            processing_notes.append(f"Fixed isGourmet flag: {total_time} min total, changed from {old_value} to {correct_gourmet}")

        # AUTO-FIX: Image URL format
        if not response_json['imageURL']["S"].startswith('menu-item-images/'):
            old_url = response_json['imageURL']["S"]
            # Extract filename and fix
            filename = old_url.split('/')[-1] if '/' in old_url else old_url
            if not filename.strip():
                filename = "default_recipe.jpg"
            response_json['imageURL']["S"] = f"menu-item-images/{filename}"
            processing_notes.append(f"Fixed imageURL format: added menu-item-images/ prefix")
        
        if not response_json['imageThumbURL']["S"].startswith('menu-item-images/'):
            old_url = response_json['imageThumbURL']["S"]
            # Extract filename and fix
            filename = old_url.split('/')[-1] if '/' in old_url else old_url
            if not filename.strip():
                filename = "default_recipe_thumbnail.jpg"
            response_json['imageThumbURL']["S"] = f"menu-item-images/{filename}"
            processing_notes.append(f"Fixed imageThumbURL format: added menu-item-images/ prefix")

        # AUTO-FIX: Primary flag logic
        if response_json['dishType']["S"] == "main" and not response_json['primary']["BOOL"]:
            response_json['primary']["BOOL"] = True
            processing_notes.append("Fixed primary flag: set to true for main dish")
        elif response_json['dishType']["S"] == "side" and response_json['primary']["BOOL"]:
            response_json['primary']["BOOL"] = False
            processing_notes.append("Fixed primary flag: set to false for side dish")

        # AUTO-FIX: flagged should always be false
        if response_json['flagged']["BOOL"] is not False:
            response_json['flagged']["BOOL"] = False
            processing_notes.append("Fixed flagged field: set to false")

        # Validate comboIndex structure (should be a Map)
        if not isinstance(response_json['comboIndex'].get("M"), dict):
            response_json['comboIndex'] = {"M": {}}
            processing_notes.append("Fixed comboIndex: set to empty map structure")

        return processing_notes

    except KeyError as e:
        processing_notes.append(f"Missing required field: {str(e)}")
        return processing_notes
    except Exception as e:
        processing_notes.append(f"Validation error: {str(e)}")
        return processing_notes

def clean_recipe_text(recipe_text: str) -> str:
    """
    Clean and sanitize the recipe text.
    """
    # Remove unwanted advertisements or other unrelated content
    lines = recipe_text.splitlines()
    clean_lines = []
    
    for line in lines:
        line = line.strip()
        # Skip empty lines and common ad markers
        if not line:
            continue
        if any(marker in line.lower() for marker in [
            "sponsored content", "advertisement", "promoted content", 
            "click here", "subscribe now"
        ]):
            continue
        clean_lines.append(line)
    
    return "\n".join(clean_lines)

def retry_with_exponential_backoff(func, max_retries=3):
    """
    Decorator for implementing exponential backoff.
    """
    def wrapper(*args, **kwargs):
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = (2 ** attempt) + (random.random() * 0.1)
                logger.warning(f"Attempt {attempt + 1} failed. Retrying in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
    return wrapper

@retry_with_exponential_backoff
def invoke_claude(prompt: str) -> Dict[str, Any]:
    """
    Invoke Claude model with retry logic.
    """
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 1,
        "top_k": 250,
        "top_p": 0.999,
        "messages": [
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
    }

    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(request_body),
        contentType="application/json",
        accept="application/json"
    )

    response_body = json.loads(response.get('body').read())
    response_text = response_body['content'][0]['text']
    
    # Log the raw response from Claude
    logger.info(f"Raw response from Claude: {response_text}")

    # Now attempt to parse the JSON
    try:
        # Remove any potential markdown formatting
        if response_text.startswith('```json'):
            response_text = response_text.replace('```json', '').replace('```', '').strip()
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude's response as JSON: {response_text}")
        raise ValueError(f"Invalid JSON response from Claude: {str(e)}")

def lambda_handler(event, context):
    # Generate request ID for tracking
    request_id = context.aws_request_id
    logger.info(f"Processing request {request_id}. Event: {json.dumps(event)}")

    try:
        # Parse the input to get the current recipe text
        input_body = event.get('recipe', '')
        if not input_body:
            input_body = json.loads(event.get('body', '{}')).get('recipe', '')

        # Validate input
        validate_input(input_body)

        # Clean the recipe text
        recipe_details = clean_recipe_text(input_body)

        # Generate unique ID for this recipe (lowercase UUID)
        recipe_id = str(uuid.uuid4()).lower()

        # Construct the enhanced prompt - REMOVED processingNotes from Claude prompt
        prompt = f"""
        Convert detailed recipe information into a JSON file adhering to the specified schema and formatting compatible with DynamoDB's requirements. Ensure the output uses the specified fields:

        Recipe Details:
        {recipe_details}

        1. Convert Recipe Details to DynamoDB-Compatible JSON Schema:
        Use the correct data type indicators ("S", "N", "BOOL", "L", "M").

        Required Fields:
        id ("S"): Use this unique ID: "{recipe_id}"
        title ("S"): The exact name of the recipe.
        dishType ("S"): "main" for main dishes, "side" for side dishes
        primary ("BOOL"): true for main dishes, false for side dishes
        baseMainId ("S"): Empty string "" (placeholder for combos)
        imageURL ("S"): Construct the filename using menu-item-images/[recipe_name].[extension], replacing spaces with underscores (_) in [recipe_name].
        imageThumbURL ("S"): Construct the filename using menu-item-images/[recipe_name_thumbnail].[extension], replacing spaces with underscores (_) in [recipe_name].
        description ("S"): A concise, engaging summary of the recipe that makes readers want to try it out, add some reference to the cuisine type if applicable.
        link ("S"): Extract any URL from the recipe text, or empty string "" if none found
        prepTime ("N"): Preparation time in minutes.
        cookTime ("N"): Cooking time in minutes.
        rating ("N"): 5
        servings ("S"): Number of servings (e.g., "4", "6-8")
        cuisineType ("S"): Type of cuisine from this list ["Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"]
        isQuick ("BOOL") : true if prepTime + cookTime is 0-30 min, else false
        isBalanced ("BOOL"): true if prepTime + cookTime is 35-60 min, else false
        isGourmet ("BOOL"): true if prepTime + cookTime is > 60 min, else false
        ingredients ("L"): A list of ingredients, preserve original quantities and convert to mixed fractions if needed (e.g., "1/2" not "0.5"), avoid special characters (e.g. inches not ")
        ingredient_objects ("L"): Empty list [] (placeholder)
        instructions ("L"): Enhance Cooking Instructions: Beginning each step with an imperative verb, Break complex steps into simpler steps easy for beginners to follow, Include ingredient quantities within the instructions for ease of use and clarity. 
        notes ("L"): Notes should be limited, and include gluten free and/or other dietary substitutes like: use gluten free noodles, flour, etc. Remove notes that are not directly related to making the recipe or substitutions and/or variations of the recipe
        recommendedSides ("L"): Empty list [] (placeholder)
        includedSides ("L"): Empty list [] (placeholder)
        comboIndex ("M"): Empty map {{}} (placeholder)
        products ("L"): Empty list [] (placeholder)
        glutenFree ("BOOL"): Default to true
        vegetarian ("BOOL"): Determine based on ingredients.
        slowCook ("BOOL"): true if the recipe uses a slow cooker.
        instaPot ("BOOL"): true if the recipe uses an Instant Pot.
        flagged ("BOOL"): Always set to false.

        Only return the json format, no additional text or comments
        """

        # Log the prompt
        logger.info(f"Request {request_id} - Prompt: {prompt}")

        # Get response from Claude
        response_json = invoke_claude(prompt)
        logger.info(f"Parsed response from Claude: {json.dumps(response_json, indent=2)}")

        # Validate the response and auto-fix issues - get processing notes separately
        processing_notes = validate_and_fix_response_schema(response_json)

        # Log processing notes
        if processing_notes:
            logger.info(f"Processing completed with notes: {processing_notes}")
        else:
            logger.info("Processing completed with no issues")

        # Return successful response with CLEAN JSON and separate processing notes
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'POST, OPTIONS'
            },
            'body': json.dumps(response_json),  # Clean recipe JSON
            'processingNotes': processing_notes  # Separate processing notes
        }

    except json.JSONDecodeError as e:
        logger.error(f"Request {request_id} - JSON parsing error: {e}")
        return {
            'statusCode': 400,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Invalid JSON format'})
        }
    except ValueError as e:
        logger.error(f"Request {request_id} - Validation error: {e}")
        return {
            'statusCode': 400,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': str(e)})
        }
    except ClientError as e:
        logger.error(f"Request {request_id} - Bedrock client error: {e}")
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Service unavailable'})
        }
    except Exception as e:
        logger.error(f"Request {request_id} - Unexpected error: {e}")
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Internal server error'})
        }
