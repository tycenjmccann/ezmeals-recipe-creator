import json
import boto3
import logging
from botocore.config import Config
from botocore.exceptions import ClientError
from typing import Dict, List, Any
import re

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configure Bedrock client
config = Config(
    region_name="us-west-2",
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
)

client = boto3.client("bedrock-runtime", config=config)

# Ordered profile IDs (cross-Region pool) - NEW MODEL FALLBACK SYSTEM
MODEL_PROFILES = [
    "us.anthropic.claude-opus-4-6-v1",              # Claude 4.6 Opus - highest quality
    "us.anthropic.claude-sonnet-4-20250514-v1:0",   # Claude 4 Sonnet - good balance, ~65K context
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0"  # Claude 3.7 Sonnet - reliable fallback
]

def invoke_claude(messages: list, cfg: dict):
    """Try each profile until one succeeds; bubble up other errors."""
    last_error = None
    
    for pid in MODEL_PROFILES:
        try:
            logger.info(f"Attempting to use model profile: {pid}")
            response = client.converse(
                modelId=pid,
                messages=messages,
                inferenceConfig=cfg
            )
            logger.info(f"Successfully used model profile: {pid}")
            return response
        except client.exceptions.AccessDeniedException as e:
            # profile exists but model not enabled in this account/Region
            logger.warning(f"Access denied for model profile {pid}: {str(e)}")
            last_error = e
            continue
        except client.exceptions.ThrottlingException as e:
            # Region-local capacity full; profile will auto-route,
            # but keep trying next profile if all Regions saturate
            logger.warning(f"Throttling for model profile {pid}: {str(e)}")
            last_error = e
            continue
        except Exception as e:
            logger.warning(f"Error with model profile {pid}: {str(e)}")
            last_error = e
            continue
    
    # If we get here, all models failed
    error_msg = f"No Claude profile is currently available. Last error: {str(last_error)}"
    logger.error(error_msg)
    raise RuntimeError(error_msg)

# Constants
VALID_CATEGORIES = {
    "Produce", "Proteins", "Dairy", "Grains & Bakery",
    "Pantry Staples", "Seasonings", "Condiments & Sauces",
    "Frozen", "Beverages", "Other"
}

def parse_ingredient_string(ingredient_str: str) -> Dict[str, str]:
    """
    Parse an ingredient string into components.
    Returns dict with quantity, unit, ingredient_name, and note.
    """
    # Remove extra whitespace
    ingredient_str = ingredient_str.strip()
    
    # Pattern to match quantity, unit, ingredient name, and optional note
    # Examples: "2 cups flour", "1 lb ground beef, lean", "3 large eggs"
    pattern = r'^(\d+(?:\s*\d+/\d+|\.\d+)?)\s*([a-zA-Z]*)\s+(.+?)(?:,\s*(.+))?$'
    
    match = re.match(pattern, ingredient_str)
    if match:
        quantity = match.group(1).strip()
        unit = match.group(2).strip() if match.group(2) else ""
        ingredient_name = match.group(3).strip()
        note = match.group(4).strip() if match.group(4) else ""
    else:
        # Fallback: treat entire string as ingredient name
        quantity = ""
        unit = ""
        ingredient_name = ingredient_str
        note = ""
    
    return {
        "quantity": quantity,
        "unit": unit,
        "ingredient_name": ingredient_name,
        "note": note
    }

def categorize_ingredient(ingredient_name: str) -> str:
    """
    Categorize an ingredient based on its name.
    Returns one of the valid categories.
    """
    ingredient_lower = ingredient_name.lower()
    
    # Produce
    if any(word in ingredient_lower for word in [
        'onion', 'garlic', 'tomato', 'pepper', 'carrot', 'celery', 'potato',
        'lettuce', 'spinach', 'broccoli', 'cauliflower', 'cucumber', 'mushroom',
        'lemon', 'lime', 'apple', 'banana', 'berry', 'herb', 'basil', 'parsley',
        'cilantro', 'thyme', 'rosemary', 'oregano', 'mint'
    ]):
        return "Produce"
    
    # Proteins
    if any(word in ingredient_lower for word in [
        'chicken', 'beef', 'pork', 'fish', 'salmon', 'tuna', 'shrimp', 'turkey',
        'lamb', 'bacon', 'sausage', 'ham', 'egg', 'tofu', 'beans', 'lentil',
        'chickpea', 'quinoa'
    ]):
        return "Proteins"
    
    # Dairy
    if any(word in ingredient_lower for word in [
        'milk', 'cream', 'butter', 'cheese', 'yogurt', 'sour cream', 'cottage cheese'
    ]):
        return "Dairy"
    
    # Grains & Bakery
    if any(word in ingredient_lower for word in [
        'flour', 'bread', 'pasta', 'rice', 'oats', 'barley', 'wheat', 'noodle',
        'tortilla', 'bagel', 'roll'
    ]):
        return "Grains & Bakery"
    
    # Pantry Staples
    if any(word in ingredient_lower for word in [
        'oil', 'vinegar', 'sugar', 'honey', 'syrup', 'vanilla', 'baking powder',
        'baking soda', 'cornstarch', 'broth', 'stock', 'wine', 'coconut milk'
    ]):
        return "Pantry Staples"
    
    # Seasonings
    if any(word in ingredient_lower for word in [
        'salt', 'pepper', 'paprika', 'cumin', 'chili', 'garlic powder',
        'onion powder', 'cinnamon', 'nutmeg', 'ginger', 'turmeric', 'curry'
    ]):
        return "Seasonings"
    
    # Condiments & Sauces
    if any(word in ingredient_lower for word in [
        'sauce', 'ketchup', 'mustard', 'mayo', 'dressing', 'paste', 'jam',
        'jelly', 'pickle', 'relish'
    ]):
        return "Condiments & Sauces"
    
    # Default to Other
    return "Other"

def call_claude_for_ingredient_objects(ingredients_list: List[str]) -> str:
    """
    Call Claude to create ingredient objects from ingredient strings.
    """
    ingredients_text = '\n'.join([f"- {ing}" for ing in ingredients_list])
    
    prompt = f"""
Convert the following ingredient strings into structured ingredient objects. Each ingredient should be parsed into its components and categorized.

Ingredients to process:
{ingredients_text}

For each ingredient, create an object with these fields:
- ingredient_name: The main ingredient name (cleaned and standardized)
- category: One of these categories: {', '.join(sorted(VALID_CATEGORIES))}
- quantity: The numeric amount (if present)
- unit: The unit of measurement (if present)
- note: Any additional preparation notes (chopped, diced, etc.)
- affiliate_link: Always empty string

Return ONLY a JSON array of objects in this exact format:
[
  {{
    "ingredient_name": {{"S": "Ingredient Name"}},
    "category": {{"S": "Category"}},
    "quantity": {{"S": "1"}},
    "unit": {{"S": "cup"}},
    "note": {{"S": "chopped"}},
    "affiliate_link": {{"S": ""}}
  }}
]

Rules:
1. Parse quantities carefully (handle fractions like 1/2, 1 1/2, etc.)
2. Standardize units (tsp → teaspoon, tbsp → tablespoon, etc.)
3. Clean ingredient names (remove quantities and units)
4. Categorize appropriately based on the ingredient type
5. Preserve preparation instructions in the note field
6. Always include all required fields, use empty strings if no value
"""
    
    conversation = [{"role": "user", "content": [{"text": prompt}]}]
    
    response = invoke_claude(
        conversation,
        {"maxTokens": 4096, "temperature": 0.7}
    )
    
    return response['output']['message']['content'][0]['text']

def extract_ingredient_objects_from_response(response_text: str) -> List[Dict]:
    """
    Extract ingredient objects from Claude's response.
    """
    try:
        # Find JSON array in the response
        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError("No JSON array found in response")
        
        json_str = response_text[json_start:json_end]
        ingredient_objects = json.loads(json_str)
        
        # Validate structure
        for obj in ingredient_objects:
            required_fields = ["ingredient_name", "category", "quantity", "unit", "note", "affiliate_link"]
            for field in required_fields:
                if field not in obj or "S" not in obj[field]:
                    raise ValueError(f"Invalid structure: missing {field}")
        
        return ingredient_objects
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        raise ValueError(f"Invalid JSON in response: {e}")
    except Exception as e:
        logger.error(f"Error extracting ingredient objects: {e}")
        raise

def merge_ingredient_objects_into_recipe(recipe_data: Dict, ingredient_objects: List[Dict]) -> Dict:
    """
    Merge ingredient objects into the recipe JSON structure.
    This is the BULLETPROOF MERGE that guarantees correct placement.
    """
    try:
        # Ensure ingredient_objects field exists and is properly structured
        if "ingredient_objects" not in recipe_data:
            recipe_data["ingredient_objects"] = {"L": []}
        elif not isinstance(recipe_data["ingredient_objects"], dict):
            recipe_data["ingredient_objects"] = {"L": []}
        elif "L" not in recipe_data["ingredient_objects"]:
            recipe_data["ingredient_objects"]["L"] = []
        
        # Convert ingredient objects to DynamoDB format and add to recipe
        dynamodb_objects = []
        for obj in ingredient_objects:
            # Each object is already in DynamoDB format from Claude
            dynamodb_objects.append({"M": obj})
        
        # Replace the ingredient_objects list
        recipe_data["ingredient_objects"]["L"] = dynamodb_objects
        
        logger.info(f"Successfully merged {len(ingredient_objects)} ingredient objects into recipe")
        return recipe_data
        
    except Exception as e:
        logger.error(f"Error merging ingredient objects: {e}")
        raise

def lambda_handler(event, context):
    """
    AWS Lambda handler for creating ingredient objects from ingredient strings.
    """
    logger.info("Starting ingredient objects creation")
    
    try:
        # Extract data from the event
        step_output = event.get('stepOutput', {})
        recipe_json_str = step_output.get('body', '{}')
        previous_notes = step_output.get('processingNotes', [])
        
        # Parse the recipe JSON
        recipe_data = json.loads(recipe_json_str)
        
        # Extract ingredients list
        ingredients_list_data = recipe_data.get('ingredients', {}).get('L', [])
        ingredients_list = [item.get('S', '') for item in ingredients_list_data if item.get('S')]
        
        if not ingredients_list:
            logger.warning("No ingredients found in recipe")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 3: No ingredients found to process"]
            }
        
        logger.info(f"Processing {len(ingredients_list)} ingredients for object creation")
        
        # Call Claude to create ingredient objects
        response_text = call_claude_for_ingredient_objects(ingredients_list)
        
        # Extract ingredient objects from response
        ingredient_objects = extract_ingredient_objects_from_response(response_text)
        
        # Merge ingredient objects into recipe (BULLETPROOF MERGE)
        updated_recipe = merge_ingredient_objects_into_recipe(recipe_data, ingredient_objects)
        
        # Create processing note
        processing_note = f"Step 3: Created {len(ingredient_objects)} ingredient objects from {len(ingredients_list)} ingredients"
        
        # Return updated recipe with processing notes
        return {
            'statusCode': 200,
            'body': json.dumps(updated_recipe),
            'processingNotes': previous_notes + [processing_note]
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'Invalid JSON: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 3: JSON parsing error - {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 3: Internal server error - {str(e)}"]
        }
