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

# FIXED Claude prompt template - focuses ONLY on ingredient names and units
CLAUDE_PROMPT_TEMPLATE = """
Standardize ONLY the ingredient names and units in the following list. Do NOT change preparation instructions or add recipe-specific details.

Recipe Context:
Title: {title}
Cuisine Type: {cuisine_type}

Current Ingredients:
{current_ingredients}

Available Standardized Ingredient Names:
{standardized_ingredients}

CRITICAL RULES:
1. ONLY standardize the core ingredient name (e.g., "beef chuck roast" → "Chuck Roast")
2. ONLY standardize units (e.g., "lbs" → "pounds", "tsp" → "teaspoon")
3. PRESERVE original quantities exactly (e.g., "3", "2 1/2", "1/4")
4. PRESERVE ALL preparation instructions exactly as written (chopped, diced, sliced, etc.)
5. DO NOT add preparation details from other recipes
6. DO NOT change recipe-specific cutting instructions
7. If no exact ingredient name match exists, leave the ingredient unchanged

Examples of CORRECT standardization:
- "3 lbs beef chuck roast, sliced thin" → "3 pounds Chuck Roast, sliced thin"
- "2 tsp dried oregano" → "2 teaspoons Dried Oregano"  
- "1 cup yellow onion, chopped" → "1 cup Yellow Onion, chopped"

Examples of INCORRECT standardization (DO NOT DO THIS):
- "3 pounds beef chuck roast, sliced thin" → "3 pounds chuck roast cut tall for long strands, sliced thin" ❌
- "1 cup onion, diced" → "1 cup onion, chopped" ❌
- "2 large eggs" → "2 eggs, beaten" ❌

Expected Output Format:
[
    {{"S": "standardized ingredient 1"}},
    {{"S": "standardized ingredient 2"}},
    {{"S": "standardized ingredient 3"}}
]

Focus: Ingredient names and units ONLY. Preserve everything else exactly as written.
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

def extract_core_ingredient_names(ingredients_list):
    """Extract core ingredient names for more precise filtering."""
    core_names = set()
    
    for ingredient in ingredients_list:
        # Remove quantities, units, and common preparation words
        cleaned = re.sub(r'^\d+[\d\s/]*\s*', '', ingredient)  # Remove quantities like "2 1/2"
        cleaned = re.sub(r'\b(cups?|cup|tablespoons?|tablespoon|teaspoons?|teaspoon|pounds?|pound|lbs?|lb|ounces?|ounce|oz|cloves?|clove)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(chopped|diced|sliced|minced|crushed|ground|fresh|dried|large|small|medium)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'[,\-\(\)]', ' ', cleaned)  # Remove punctuation
        
        # Extract meaningful words (3+ characters)
        words = [word.strip() for word in cleaned.split() if len(word.strip()) >= 3]
        core_names.update([word.lower() for word in words])
    
    return core_names

def get_filtered_standardized_ingredients(credentials, ingredients_list):
    """Get standardized ingredients filtered by core ingredient names only."""
    dynamodb = boto3.resource(
        "dynamodb",
        config=config_us_west_1,
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken']
    )
    table = dynamodb.Table(CONFIG['TABLE_NAME'])
    
    # Extract core ingredient names for precise filtering
    core_names = extract_core_ingredient_names(ingredients_list)
    logger.info(f"Extracted core ingredient names: {list(core_names)[:10]}...")
    
    try:
        # Get all standardized ingredients (cached)
        all_items = get_all_standardized_ingredients_cached(
            credentials['AccessKeyId'],
            credentials['SecretAccessKey'], 
            credentials['SessionToken']
        )
        
        # Filter to only relevant standardized ingredients based on core names
        relevant_items = []
        for item in all_items:
            ingredient_name = item.get('ingredient_name', '').lower()
            # Check if any core name matches the standardized ingredient name
            if any(core_name in ingredient_name or ingredient_name in core_name for core_name in core_names):
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
    """Generate focused prompt for Claude with emphasis on names and units only."""
    # Extract only ingredient names (no preparation details) for Claude
    standardized_names = []
    for item in standardized_ingredients:
        name = item.get('ingredient_name', '').strip()
        if name:
            standardized_names.append(name)
    
    # Remove duplicates and sort for consistency
    standardized_names = sorted(list(set(standardized_names)))
    
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
                inferenceConfig={"maxTokens": 4096, "temperature": 0.3},  # Lower temperature for more consistent results
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

def validate_updated_ingredients(updated_ingredients, original_ingredients, original_count):
    """Validate the updated ingredients and check for unwanted changes."""
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
    
    # Check for unwanted preparation changes
    for i, (original, updated) in enumerate(zip(original_ingredients, updated_ingredients)):
        original_text = original.lower()
        updated_text = updated['S'].lower()
        
        # Check if preparation instructions were inappropriately added
        suspicious_additions = [
            'cut tall for long strands',
            'beaten until frothy',
            'finely chopped',
            'thinly sliced'
        ]
        
        for addition in suspicious_additions:
            if addition in updated_text and addition not in original_text:
                logger.warning(f"Suspicious preparation addition detected in ingredient {i}: '{addition}'")
                logger.warning(f"Original: {original}")
                logger.warning(f"Updated: {updated['S']}")
    
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
    """Main Lambda handler with focused ingredient name and unit standardization."""
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting focused ingredient standardization (names and units only)")
    
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
    
    # Create focused prompt
    logger.info(f"[{request_id}] Creating focused Claude prompt (names and units only)")
    prompt = create_claude_prompt(ingredients_list, standardized_ingredients, recipe_context)
    logger.info(f"[{request_id}] Prompt size: {len(prompt)} characters")
    
    # Get updated ingredients from Claude
    logger.info(f"[{request_id}] Invoking Claude with focused standardization")
    conversation = [{"role": "user", "content": [{"text": prompt}]}]
    claude_response = call_bedrock_api(conversation)
    response_text = claude_response["output"]["message"]["content"][0]["text"]
    
    # Extract and validate updated ingredients
    logger.info(f"[{request_id}] Extracting updated ingredients from response")
    updated_ingredients = extract_updated_ingredients(response_text)
    
    logger.info(f"[{request_id}] Validating updated ingredients for unwanted changes")
    validate_updated_ingredients(updated_ingredients, ingredients_list, len(ingredients_list))
    
    # Merge back into original JSON
    logger.info(f"[{request_id}] Merging updated ingredients into original JSON")
    updated_json = merge_updated_ingredients(step_output_data, updated_ingredients, request_id)
    
    logger.info(f"[{request_id}] Successfully completed focused ingredient standardization")
    return {
        'statusCode': 200,
        'body': json.dumps(updated_json)
    }
    
    # NO EXCEPTION HANDLERS - Let all errors bubble up clearly
