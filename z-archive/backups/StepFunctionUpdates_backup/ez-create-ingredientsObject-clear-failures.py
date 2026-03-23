import json
import boto3
import logging
import re
from botocore.config import Config
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configure the Boto3 client with extended timeout settings and retry logic
config = Config(
    region_name="us-west-2",
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 10, 'mode': 'standard'}
)

# Create a Bedrock Runtime client with the updated config
client = boto3.client("bedrock-runtime", config=config)

# Constants
MODEL_ID = "anthropic.claude-3-opus-20240229-v1:0"
VALID_CATEGORIES = {
    "Produce", "Proteins", "Dairy", "Grains & Bakery",
    "Pantry Staples", "Seasonings", "Frozen Foods"
}

# Optimized prompt template - requests only ingredient_objects
CLAUDE_PROMPT_TEMPLATE = """
Parse the following ingredients into structured objects for a recipe database.

Recipe Context (for categorization hints):
Title: {title}
Cuisine Type: {cuisine_type}

Ingredients to Parse:
{ingredients_json}

Instructions:
- Parse each ingredient string into the exact DynamoDB format shown below
- Extract quantity, unit, ingredient name, and preparation notes
- Move descriptors (large, fresh, chopped, diced, minced) to the note field
- Ingredient names should be capitalized (e.g., "Yellow Onion", "Ground Beef")
- Categories must be one of: {categories}
- Return ONLY the ingredient_objects structure as valid JSON, no other text or explanations

Expected Output Format (return exactly this structure):
{{
    "L": [
        {{
            "M": {{
                "ingredient_name": {{"S": "Ingredient Name"}},
                "category": {{"S": "Category"}},
                "quantity": {{"S": "Amount"}},
                "unit": {{"S": "Unit"}},
                "note": {{"S": "Preparation notes"}},
                "affiliate_link": {{"S": ""}}
            }}
        }}
    ]
}}

Parsing Examples:
- "1 cup yellow onion, chopped" → ingredient_name: "Yellow Onion", quantity: "1", unit: "cup", note: "chopped"
- "2 large eggs, beaten" → ingredient_name: "Eggs", quantity: "2", unit: "", note: "large, beaten"
- "Salt and pepper to taste" → ingredient_name: "Salt", quantity: "", unit: "", note: "to taste"
"""

def validate_input(event):
    """Validate and extract required input from the event."""
    recipe_text = event.get('recipe')
    step_output = event.get('stepOutput', {}).get('body')
    
    if not recipe_text or not step_output:
        raise ValueError(
            f"Missing required inputs: recipe={'✓' if recipe_text else '✗'}, "
            f"stepOutput.body={'✓' if step_output else '✗'}"
        )
    
    try:
        return recipe_text, json.loads(step_output)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in stepOutput.body: {e}")

def extract_recipe_context(step_output_data):
    """Extract minimal context needed for ingredient categorization."""
    return {
        'title': step_output_data.get('title', {}).get('S', 'Unknown Recipe'),
        'cuisine_type': step_output_data.get('cuisineType', {}).get('S', 'Unknown')
    }

def extract_ingredients_list(step_output_data):
    """Extract the ingredients list from the JSON."""
    ingredients = step_output_data.get('ingredients', {}).get('L', [])
    if not ingredients:
        raise ValueError("No ingredients found in recipe data")
    
    # Convert from DynamoDB format to simple list for Claude
    ingredients_list = [item.get('S', '') for item in ingredients]
    ingredients_list = [ing for ing in ingredients_list if ing.strip()]  # Remove empty strings
    
    if not ingredients_list:
        raise ValueError("All ingredients are empty or invalid")
    
    return ingredients_list

def create_claude_prompt(ingredients_list, recipe_context):
    """Create optimized prompt that requests only ingredient_objects."""
    return CLAUDE_PROMPT_TEMPLATE.format(
        title=recipe_context['title'],
        cuisine_type=recipe_context['cuisine_type'],
        ingredients_json=json.dumps(ingredients_list, indent=2),
        categories=', '.join(VALID_CATEGORIES)
    )

def invoke_claude(prompt):
    """Invoke Claude model and return the response."""
    try:
        response = client.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0.7},
            additionalModelRequestFields={"top_k": 250}
        )
        return response["output"]["message"]["content"][0]["text"]
    except ClientError as e:
        logger.error(f"Bedrock API error: {e.response['Error']['Code']} - {e}")
        raise  # Re-raise the original exception - NO MASKING

def extract_ingredient_objects(response_text):
    """Extract and validate the ingredient_objects structure from Claude's response."""
    logger.info(f"Raw Claude response length: {len(response_text)} characters")
    
    # Look for the specific L array structure
    json_match = re.search(r'{\s*"L":\s*\[.*?\]\s*}', response_text, re.DOTALL)
    if not json_match:
        # Fallback: look for any JSON structure
        json_match = re.search(r'({.*})', response_text, re.DOTALL)
        if not json_match:
            logger.error(f"No JSON found in Claude response: {response_text}")
            raise ValueError("No JSON structure found in Claude's response")
    
    json_str = json_match.group(0)
    logger.info(f"Extracted JSON string length: {len(json_str)} characters")
    
    try:
        ingredient_objects = json.loads(json_str)
        logger.info(f"Successfully parsed ingredient_objects with {len(ingredient_objects.get('L', []))} items")
        return ingredient_objects
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed. Extracted string: {json_str}")
        raise ValueError(f"Invalid JSON in Claude's response: {e}")

def validate_ingredient_objects(ingredient_objects, original_ingredients_count):
    """Validate the ingredient_objects structure."""
    if not isinstance(ingredient_objects, dict):
        raise ValueError("ingredient_objects must be a dictionary")
    
    if 'L' not in ingredient_objects:
        raise ValueError("ingredient_objects missing required 'L' array")
    
    if not isinstance(ingredient_objects['L'], list):
        raise ValueError("ingredient_objects 'L' must be an array")
    
    objects_count = len(ingredient_objects['L'])
    if objects_count == 0:
        raise ValueError("No ingredient objects were created")
    
    # Validate each object structure
    for i, obj in enumerate(ingredient_objects['L']):
        if not isinstance(obj, dict) or 'M' not in obj:
            raise ValueError(f"Ingredient object {i} missing required 'M' structure")
        
        required_fields = ['ingredient_name', 'category', 'quantity', 'unit', 'note', 'affiliate_link']
        for field in required_fields:
            if field not in obj['M']:
                raise ValueError(f"Ingredient object {i} missing required field '{field}'")
            if 'S' not in obj['M'][field]:
                raise ValueError(f"Ingredient object {i} field '{field}' missing 'S' value")
    
    # Log count difference (not an error - Claude might combine ingredients)
    if objects_count != original_ingredients_count:
        logger.warning(f"Ingredient count difference: {objects_count} objects vs {original_ingredients_count} original ingredients")
    
    logger.info(f"Validation successful: {objects_count} ingredient objects created")
    return True

def merge_ingredient_objects(original_json, ingredient_objects):
    """Safely merge ingredient_objects back into the original JSON."""
    original_json['ingredient_objects'] = ingredient_objects
    logger.info("Successfully merged ingredient_objects into original JSON")
    return original_json

def lambda_handler(event, context):
    """Main Lambda handler function with clear failure approach - NO ERROR MASKING."""
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting ingredient processing")
    
    # Validate and extract input - let validation errors bubble up
    logger.info(f"[{request_id}] Validating input")
    recipe_text, step_output_data = validate_input(event)
    
    # Extract recipe context - let extraction errors bubble up
    logger.info(f"[{request_id}] Extracting recipe context")
    recipe_context = extract_recipe_context(step_output_data)
    
    # Extract ingredients list - let extraction errors bubble up
    logger.info(f"[{request_id}] Extracting ingredients list")
    ingredients_list = extract_ingredients_list(step_output_data)
    logger.info(f"[{request_id}] Processing {len(ingredients_list)} ingredients for recipe: {recipe_context['title']}")
    
    # Create optimized prompt
    logger.info(f"[{request_id}] Creating Claude prompt")
    prompt = create_claude_prompt(ingredients_list, recipe_context)
    logger.info(f"[{request_id}] Prompt size: {len(prompt)} characters")
    
    # Get ingredient_objects from Claude - let API errors bubble up
    logger.info(f"[{request_id}] Invoking Claude")
    claude_response = invoke_claude(prompt)
    
    # Extract and validate ingredient_objects - let parsing errors bubble up
    logger.info(f"[{request_id}] Extracting ingredient objects from response")
    ingredient_objects = extract_ingredient_objects(claude_response)
    
    logger.info(f"[{request_id}] Validating ingredient objects")
    validate_ingredient_objects(ingredient_objects, len(ingredients_list))
    
    # Merge back into original JSON
    logger.info(f"[{request_id}] Merging ingredient objects into original JSON")
    updated_json = merge_ingredient_objects(step_output_data, ingredient_objects)
    
    logger.info(f"[{request_id}] Successfully processed ingredient objects")
    return {
        'statusCode': 200,
        'body': json.dumps(updated_json)
    }
    
    # NO EXCEPTION HANDLERS - Let all errors bubble up clearly
    # This ensures Step Functions sees exactly what went wrong
    # Retries will be handled at the Step Functions level
