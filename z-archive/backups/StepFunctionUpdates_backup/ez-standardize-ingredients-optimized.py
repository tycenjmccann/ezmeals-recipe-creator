import json
import boto3
import logging
import re
import time
from functools import lru_cache
from botocore.config import Config
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
CONFIG = {
    'MODEL_ID': "anthropic.claude-3-opus-20240229-v1:0",
    'ROLE_ARN': "arn:aws:iam::970547358447:role/IsengardAccount-DynamoDBAccess",
    'TABLE_NAME': "Ingredient-ryvykzwfevawxbpf5nmynhgtea-dev",
    'REGIONS': {
        'DYNAMODB': "us-west-1",
        'BEDROCK': "us-west-2"
    }
}

# AWS Configurations
config_us_west_1 = Config(
    region_name=CONFIG['REGIONS']['DYNAMODB'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 10, 'mode': 'standard'}
)

config_us_west_2 = Config(
    region_name=CONFIG['REGIONS']['BEDROCK'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 10, 'mode': 'standard'}
)

# Initialize AWS clients
bedrock_client = boto3.client("bedrock-runtime", config=config_us_west_2)
sts_client = boto3.client("sts", config=config_us_west_2)

# Optimized Claude prompt template - requests only updated ingredients
CLAUDE_PROMPT_TEMPLATE = """
Standardize the ingredient names in the following list using the provided standardized ingredient names.

Recipe Context:
Title: {title}
Cuisine Type: {cuisine_type}

Current Ingredients:
{current_ingredients}

Available Standardized Ingredients:
{standardized_ingredients}

Instructions:
- Only update ingredient names that have exact or very close matches in the standardized list
- Preserve original quantities, units, and preparation notes exactly as they are
- Keep mixed fractions (e.g., "1/2" not "0.5")
- Do not modify ingredients that don't have clear matches
- Return ONLY the updated ingredients list in the exact same DynamoDB format

Expected Output Format:
[
    {{"S": "updated ingredient 1"}},
    {{"S": "updated ingredient 2"}},
    {{"S": "updated ingredient 3"}}
]

Examples:
- "1 cup yellow onion, chopped" → "1 cup Yellow Onion, chopped" (if "Yellow Onion" is in standardized list)
- "2 lbs ground beef" → "2 lbs Ground Beef" (if "Ground Beef" is in standardized list)
- "Salt to taste" → "Salt to taste" (keep as-is if exact match exists)
"""

def validate_input(event):
    """Validate input event structure and required fields"""
    if not isinstance(event, dict):
        raise ValueError("Event must be a dictionary")
    
    recipe_text = event.get('recipe')
    step_output = event.get('stepOutput', {}).get('body')
    
    if not recipe_text or not step_output:
        raise ValueError("Missing required fields: 'recipe' or 'stepOutput.body'")
    
    return recipe_text, step_output

def assume_cross_account_role():
    """Assumes a role in another AWS account and returns temporary credentials."""
    try:
        response = sts_client.assume_role(
            RoleArn=CONFIG['ROLE_ARN'],
            RoleSessionName="StandardizedIngredientsAccessSession"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise  # Don't mask the error

def extract_recipe_context(step_output_data):
    """Extract minimal context for ingredient standardization."""
    return {
        'title': step_output_data.get('title', {}).get('S', 'Unknown Recipe'),
        'cuisine_type': step_output_data.get('cuisineType', {}).get('S', 'Unknown')
    }

def extract_ingredients_list(step_output_data):
    """Extract the current ingredients list from the JSON."""
    ingredients = step_output_data.get('ingredients', {}).get('L', [])
    if not ingredients:
        raise ValueError("No ingredients found in recipe data")
    
    # Convert from DynamoDB format to simple list
    ingredients_list = [item.get('S', '') for item in ingredients]
    ingredients_list = [ing for ing in ingredients_list if ing.strip()]
    
    if not ingredients_list:
        raise ValueError("All ingredients are empty or invalid")
    
    return ingredients_list

def get_filtered_standardized_ingredients(credentials, ingredients_list):
    """Get standardized ingredients filtered by relevance to current ingredients."""
    dynamodb = boto3.resource(
        "dynamodb",
        config=config_us_west_1,
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken']
    )
    table = dynamodb.Table(CONFIG['TABLE_NAME'])
    
    # Extract key words from current ingredients for filtering
    ingredient_keywords = set()
    for ingredient in ingredients_list:
        # Extract potential ingredient names (remove quantities, units, prep notes)
        words = re.findall(r'\b[A-Za-z]{3,}\b', ingredient.lower())
        ingredient_keywords.update(words)
    
    logger.info(f"Extracted keywords for filtering: {list(ingredient_keywords)[:10]}...")  # Log first 10
    
    try:
        # Get all standardized ingredients (cached)
        all_items = get_all_standardized_ingredients_cached(
            credentials['AccessKeyId'],
            credentials['SecretAccessKey'], 
            credentials['SessionToken']
        )
        
        # Filter to only relevant standardized ingredients
        relevant_items = []
        for item in all_items:
            ingredient_name = item.get('ingredient_name', '').lower()
            # Check if any keyword matches the standardized ingredient name
            if any(keyword in ingredient_name or ingredient_name in keyword for keyword in ingredient_keywords):
                relevant_items.append(item)
        
        logger.info(f"Filtered standardized ingredients: {len(relevant_items)} relevant out of {len(all_items)} total")
        return relevant_items
        
    except ClientError as e:
        logger.error(f"Error retrieving ingredients from DynamoDB: {e}")
        raise  # Don't mask the error

@lru_cache(maxsize=1)
def get_all_standardized_ingredients_cached(access_key, secret_key, session_token):
    """Cached version of all standardized ingredients fetch."""
    dynamodb = boto3.resource(
        "dynamodb",
        config=config_us_west_1,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token
    )
    table = dynamodb.Table(CONFIG['TABLE_NAME'])
    
    items = []
    last_evaluated_key = None
    
    try:
        while True:
            scan_kwargs = {}
            if last_evaluated_key:
                scan_kwargs['ExclusiveStartKey'] = last_evaluated_key
                
            response = table.scan(**scan_kwargs)
            items.extend(response.get('Items', []))
            
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break
                
        return items
    
    except ClientError as e:
        logger.error(f"Error retrieving ingredients from DynamoDB: {e}")
        raise

def create_claude_prompt(ingredients_list, standardized_ingredients, recipe_context):
    """Generate optimized prompt for Claude with filtered ingredients."""
    # Simplify standardized ingredients for Claude
    standardized_names = [item.get('ingredient_name', '') for item in standardized_ingredients]
    standardized_names = [name for name in standardized_names if name.strip()]
    
    return CLAUDE_PROMPT_TEMPLATE.format(
        title=recipe_context['title'],
        cuisine_type=recipe_context['cuisine_type'],
        current_ingredients=json.dumps(ingredients_list, indent=2),
        standardized_ingredients=json.dumps(standardized_names, indent=2)
    )

def call_bedrock_api(conversation, max_retries=3):
    """Call Bedrock API with retry logic"""
    for attempt in range(max_retries):
        try:
            return bedrock_client.converse(
                modelId=CONFIG['MODEL_ID'],
                messages=conversation,
                inferenceConfig={"maxTokens": 4096, "temperature": 0.7},
                additionalModelRequestFields={"top_k": 250}
            )
        except ClientError as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"API call failed, retrying (attempt {attempt + 1}/{max_retries})")
            time.sleep(2 ** attempt)  # Exponential backoff

def extract_updated_ingredients(response_text):
    """Extract the updated ingredients list from Claude's response."""
    logger.info(f"Raw Claude response length: {len(response_text)} characters")
    
    # Look for JSON array structure
    json_match = re.search(r'\[.*?\]', response_text, re.DOTALL)
    if not json_match:
        logger.error(f"No JSON array found in Claude response: {response_text}")
        raise ValueError("No ingredients array found in Claude's response")
    
    json_str = json_match.group(0)
    logger.info(f"Extracted JSON array length: {len(json_str)} characters")
    
    try:
        updated_ingredients = json.loads(json_str)
        if not isinstance(updated_ingredients, list):
            raise ValueError("Claude response must be a list of ingredients")
        
        logger.info(f"Successfully parsed {len(updated_ingredients)} updated ingredients")
        return updated_ingredients
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed. Extracted string: {json_str}")
        raise ValueError(f"Invalid JSON in Claude's response: {e}")

def validate_updated_ingredients(updated_ingredients, original_count):
    """Validate the updated ingredients structure."""
    if not isinstance(updated_ingredients, list):
        raise ValueError("Updated ingredients must be a list")
    
    if len(updated_ingredients) == 0:
        raise ValueError("No updated ingredients returned")
    
    # Validate each ingredient structure
    for i, ingredient in enumerate(updated_ingredients):
        if not isinstance(ingredient, dict):
            raise ValueError(f"Ingredient {i} must be a dictionary")
        if 'S' not in ingredient:
            raise ValueError(f"Ingredient {i} missing required 'S' field")
        if not isinstance(ingredient['S'], str):
            raise ValueError(f"Ingredient {i} 'S' field must be a string")
        if not ingredient['S'].strip():
            raise ValueError(f"Ingredient {i} cannot be empty")
    
    # Log count difference (not necessarily an error)
    if len(updated_ingredients) != original_count:
        logger.warning(f"Ingredient count difference: {len(updated_ingredients)} updated vs {original_count} original")
    
    logger.info(f"Validation successful: {len(updated_ingredients)} updated ingredients")
    return True

def merge_updated_ingredients(original_json, updated_ingredients, request_id):
    """Safely merge updated ingredients back into the original JSON."""
    
    logger.info(f"[{request_id}] Starting ingredients merge validation")
    
    # Validate original JSON structure
    if not isinstance(original_json, dict):
        raise ValueError(f"Original JSON must be a dictionary, got: {type(original_json)}")
    
    if 'ingredients' not in original_json:
        raise ValueError("Original JSON missing 'ingredients' field for merge")
    
    if not isinstance(original_json['ingredients'], dict) or 'L' not in original_json['ingredients']:
        raise ValueError("Original ingredients field has wrong structure")
    
    logger.info(f"[{request_id}] Original JSON structure validation passed")
    
    # Store original for comparison
    original_ingredients = original_json['ingredients']['L']
    logger.info(f"[{request_id}] Replacing {len(original_ingredients)} ingredients with {len(updated_ingredients)} updated ingredients")
    
    # Perform the merge
    original_json['ingredients']['L'] = updated_ingredients
    
    # Validate the merge worked correctly
    if original_json['ingredients']['L'] != updated_ingredients:
        raise ValueError("Merge validation failed - ingredients not properly assigned")
    
    # Validate final structure
    if not isinstance(original_json['ingredients']['L'], list):
        raise ValueError("Final merged ingredients structure is invalid")
    
    final_count = len(original_json['ingredients']['L'])
    expected_count = len(updated_ingredients)
    
    if final_count != expected_count:
        raise ValueError(f"Merge count mismatch: expected {expected_count}, got {final_count}")
    
    logger.info(f"[{request_id}] Successfully merged {final_count} updated ingredients")
    logger.info(f"[{request_id}] Ingredients merge validation: All checks passed")
    
    return original_json

def lambda_handler(event, context):
    """Main Lambda handler with optimized context window usage and clear failures."""
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting ingredient standardization")
    
    # Validate input - let validation errors bubble up
    logger.info(f"[{request_id}] Validating input")
    recipe_text, step_output = validate_input(event)
    
    # Parse step output
    try:
        step_output_data = json.loads(step_output)
        logger.info(f"[{request_id}] Successfully parsed step output data")
    except json.JSONDecodeError as e:
        logger.error(f"[{request_id}] JSON parsing failed: {e}")
        raise ValueError(f"Invalid JSON in stepOutput.body: {e}")
    
    # Extract recipe context and ingredients
    logger.info(f"[{request_id}] Extracting recipe context and ingredients")
    recipe_context = extract_recipe_context(step_output_data)
    ingredients_list = extract_ingredients_list(step_output_data)
    logger.info(f"[{request_id}] Processing {len(ingredients_list)} ingredients for recipe: {recipe_context['title']}")
    
    # Get credentials and standardized ingredients
    logger.info(f"[{request_id}] Assuming cross-account role")
    credentials = assume_cross_account_role()
    
    logger.info(f"[{request_id}] Retrieving filtered standardized ingredients")
    standardized_ingredients = get_filtered_standardized_ingredients(credentials, ingredients_list)
    
    if not standardized_ingredients:
        logger.warning(f"[{request_id}] No relevant standardized ingredients found, returning original")
        return {
            'statusCode': 200,
            'body': json.dumps(step_output_data)
        }
    
    # Create optimized prompt
    logger.info(f"[{request_id}] Creating Claude prompt")
    prompt = create_claude_prompt(ingredients_list, standardized_ingredients, recipe_context)
    logger.info(f"[{request_id}] Prompt size: {len(prompt)} characters")
    
    # Get updated ingredients from Claude
    logger.info(f"[{request_id}] Invoking Claude")
    conversation = [{"role": "user", "content": [{"text": prompt}]}]
    claude_response = call_bedrock_api(conversation)
    response_text = claude_response["output"]["message"]["content"][0]["text"]
    
    # Extract and validate updated ingredients
    logger.info(f"[{request_id}] Extracting updated ingredients from response")
    updated_ingredients = extract_updated_ingredients(response_text)
    
    logger.info(f"[{request_id}] Validating updated ingredients")
    validate_updated_ingredients(updated_ingredients, len(ingredients_list))
    
    # Merge back into original JSON
    logger.info(f"[{request_id}] Merging updated ingredients into original JSON")
    updated_json = merge_updated_ingredients(step_output_data, updated_ingredients, request_id)
    
    logger.info(f"[{request_id}] Successfully standardized ingredients")
    return {
        'statusCode': 200,
        'body': json.dumps(updated_json)
    }
    
    # NO EXCEPTION HANDLERS - Let all errors bubble up clearly
    # This ensures Step Functions sees exactly what went wrong
