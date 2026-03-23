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
    'ROLE_ARN': "arn:aws:iam::970547358447:role/IsengardAccount-DynamoDBAccess",
    'TABLE_NAME': "Ingredient-ryvykzwfevawxbpf5nmynhgtea-dev",
    'REGIONS': {
        'DYNAMODB': "us-west-1",
        'BEDROCK': "us-west-2"
    }
}

# AWS Configurations - OPTIMIZED RETRY SETTINGS
config_us_west_1 = Config(
    region_name=CONFIG['REGIONS']['DYNAMODB'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 3, 'mode': 'adaptive'}  # Reduced from 10 to 3
)

config_us_west_2 = Config(
    region_name=CONFIG['REGIONS']['BEDROCK'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 2, 'mode': 'adaptive'}  # Reduced from 10 to 2 - let model fallback handle failures
)

# Initialize AWS clients
bedrock_client = boto3.client("bedrock-runtime", config=config_us_west_2)
sts_client = boto3.client("sts", config=config_us_west_2)

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
            response = bedrock_client.converse(
                modelId=pid,
                messages=messages,
                inferenceConfig=cfg
            )
            logger.info(f"Successfully used model profile: {pid}")
            return response
        except bedrock_client.exceptions.AccessDeniedException as e:
            # profile exists but model not enabled in this account/Region
            logger.warning(f"Access denied for model profile {pid}: {str(e)}")
            last_error = e
            continue
        except bedrock_client.exceptions.ThrottlingException as e:
            # Region-local capacity full; profile will auto-route,
            # but keep trying next profile if all Regions saturate
            logger.warning(f"Throttling for model profile {pid}: {str(e)} - trying next model")
            last_error = e
            continue
        except Exception as e:
            logger.warning(f"Error with model profile {pid}: {str(e)} - trying next model")
            last_error = e
            continue
    
    # If we get here, all models failed
    error_msg = f"No Claude profile is currently available. Last error: {str(last_error)}"
    logger.error(error_msg)
    raise RuntimeError(error_msg)

# ENHANCED Claude prompt template - requests ingredient changes details
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

Return your response in this exact format:

UPDATED_INGREDIENTS:
[
    {{"S": "standardized ingredient 1"}},
    {{"S": "standardized ingredient 2"}},
    {{"S": "standardized ingredient 3"}}
]

CHANGES_MADE:
- "original ingredient" → "standardized ingredient"
- "2 lbs ground beef" → "2 pounds Ground Beef"
- "1 tsp salt" → "1 teaspoon Salt"
"""

@lru_cache(maxsize=1)
def get_standardized_ingredients():
    """Fetch standardized ingredients from DynamoDB with caching"""
    try:
        # Assume cross-account role
        assumed_role = sts_client.assume_role(
            RoleArn=CONFIG['ROLE_ARN'],
            RoleSessionName='IngredientStandardization'
        )
        
        # Create DynamoDB client with assumed role credentials
        dynamodb = boto3.client(
            'dynamodb',
            region_name=CONFIG['REGIONS']['DYNAMODB'],
            aws_access_key_id=assumed_role['Credentials']['AccessKeyId'],
            aws_secret_access_key=assumed_role['Credentials']['SecretAccessKey'],
            aws_session_token=assumed_role['Credentials']['SessionToken'],
            config=config_us_west_1
        )
        
        # Scan the Ingredient table
        response = dynamodb.scan(TableName=CONFIG['TABLE_NAME'])
        
        ingredients = []
        for item in response['Items']:
            if 'ingredient_name' in item and 'S' in item['ingredient_name']:
                ingredients.append(item['ingredient_name']['S'])
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = dynamodb.scan(
                TableName=CONFIG['TABLE_NAME'],
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            for item in response['Items']:
                if 'ingredient_name' in item and 'S' in item['ingredient_name']:
                    ingredients.append(item['ingredient_name']['S'])
        
        logger.info(f"Retrieved {len(ingredients)} standardized ingredients")
        return ingredients
        
    except Exception as e:
        logger.error(f"Error fetching standardized ingredients: {e}")
        return []

def filter_relevant_ingredients(current_ingredients, standardized_ingredients):
    """Filter standardized ingredients to only those relevant to current recipe"""
    if not standardized_ingredients:
        return []
    
    # Extract base words from current ingredients for matching
    current_words = set()
    for ingredient in current_ingredients:
        # Remove quantities, units, and common preparation words
        cleaned = re.sub(r'\d+[\d\s/]*\s*(cups?|cup|tbsp|tsp|teaspoons?|tablespoons?|pounds?|lbs?|oz|ounces?|grams?|kg|liters?|ml)\s*', '', ingredient.lower())
        cleaned = re.sub(r'\b(chopped|diced|sliced|minced|crushed|fresh|dried|ground|whole|large|small|medium)\b', '', cleaned)
        words = cleaned.split()
        current_words.update(word.strip(',()') for word in words if len(word) > 2)
    
    # Filter standardized ingredients that share words with current ingredients
    relevant_ingredients = []
    for std_ingredient in standardized_ingredients:
        std_words = set(word.lower() for word in std_ingredient.split() if len(word) > 2)
        if current_words & std_words:  # If there's any intersection
            relevant_ingredients.append(std_ingredient)
    
    logger.info(f"Filtered to {len(relevant_ingredients)} relevant standardized ingredients")
    return relevant_ingredients

def call_bedrock_api(conversation, max_retries=3):
    """Call Bedrock API with retry logic using new model fallback system - NO ADDITIONAL RETRIES"""
    # The invoke_claude function handles all retries via model fallback
    # No need for additional retry logic here
    return invoke_claude(
        conversation,
        {"maxTokens": 4096, "temperature": 0.3}  # Lower temperature for more consistent results
    )

def extract_updated_ingredients_and_changes(response_text):
    """Extract both the updated ingredients and the changes made from Claude's response."""
    logger.info(f"Raw Claude response length: {len(response_text)} characters")
    
    try:
        # Extract UPDATED_INGREDIENTS section
        ingredients_match = re.search(r'UPDATED_INGREDIENTS:\s*\[(.*?)\]', response_text, re.DOTALL)
        if not ingredients_match:
            raise ValueError("Could not find UPDATED_INGREDIENTS section in response")
        
        ingredients_text = ingredients_match.group(1)
        
        # Parse the ingredients JSON array
        ingredients_json = f'[{ingredients_text}]'
        ingredients_list = json.loads(ingredients_json)
        
        # Extract CHANGES_MADE section for processing notes
        changes_text = ""
        changes_match = re.search(r'CHANGES_MADE:\s*(.*?)(?:\n\n|\Z)', response_text, re.DOTALL)
        if changes_match:
            changes_lines = changes_match.group(1).strip().split('\n')
            # Filter out empty lines and format properly
            changes = [line.strip() for line in changes_lines if line.strip() and line.strip().startswith('-')]
            if changes:
                changes_text = "; ".join(change.lstrip('- ') for change in changes)
        
        logger.info(f"Successfully extracted {len(ingredients_list)} ingredients and changes")
        return ingredients_list, changes_text
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        logger.error(f"Problematic text: {ingredients_text if 'ingredients_text' in locals() else 'N/A'}")
        raise ValueError(f"Invalid JSON in Claude response: {e}")
    except Exception as e:
        logger.error(f"Error extracting ingredients and changes: {e}")
        raise

def standardize_ingredients_with_claude(current_ingredients, recipe_title, cuisine_type):
    """Use Claude to standardize ingredients with enhanced change tracking"""
    try:
        # Get standardized ingredients from database
        all_standardized = get_standardized_ingredients()
        if not all_standardized:
            logger.warning("No standardized ingredients available, skipping standardization")
            return current_ingredients, "No standardized ingredients available"
        
        # Filter to relevant ingredients only (CONTEXT OPTIMIZATION)
        relevant_standardized = filter_relevant_ingredients(current_ingredients, all_standardized)
        if not relevant_standardized:
            logger.info("No relevant standardized ingredients found for this recipe")
            return current_ingredients, "No ingredients required standardization"
        
        # Format current ingredients for prompt
        current_ingredients_text = '\n'.join([f"- {ing}" for ing in current_ingredients])
        standardized_ingredients_text = '\n'.join([f"- {ing}" for ing in relevant_standardized])
        
        # Create the prompt
        prompt = CLAUDE_PROMPT_TEMPLATE.format(
            title=recipe_title,
            cuisine_type=cuisine_type,
            current_ingredients=current_ingredients_text,
            standardized_ingredients=standardized_ingredients_text
        )
        
        # Prepare conversation
        conversation = [
            {
                "role": "user",
                "content": [{"text": prompt}]
            }
        ]
        
        # Call Claude
        logger.info("Calling Claude for ingredient standardization")
        response = call_bedrock_api(conversation)
        response_text = response['output']['message']['content'][0]['text']
        
        # Extract results
        updated_ingredients_list, changes_made = extract_updated_ingredients_and_changes(response_text)
        
        # Convert back to simple string list
        updated_ingredients = [item['S'] for item in updated_ingredients_list]
        
        logger.info(f"Standardization completed: {len(updated_ingredients)} ingredients processed")
        return updated_ingredients, changes_made
        
    except Exception as e:
        logger.error(f"Error in ingredient standardization: {e}")
        return current_ingredients, f"Standardization failed: {str(e)}"

def lambda_handler(event, context):
    """
    AWS Lambda handler for standardizing ingredients in recipe JSON.
    """
    logger.info("Starting ingredient standardization")
    
    try:
        # Extract data from the event
        step_output = event.get('stepOutput', {})
        recipe_json_str = step_output.get('body', '{}')
        previous_notes = step_output.get('processingNotes', [])
        
        # Parse the recipe JSON
        recipe_data = json.loads(recipe_json_str)
        
        # Extract recipe context for Claude
        recipe_title = recipe_data.get('title', {}).get('S', 'Unknown Recipe')
        cuisine_type = recipe_data.get('cuisineType', {}).get('S', 'Unknown')
        
        # Extract current ingredients
        ingredients_list = recipe_data.get('ingredients', {}).get('L', [])
        current_ingredients = [item.get('S', '') for item in ingredients_list if item.get('S')]
        
        if not current_ingredients:
            logger.warning("No ingredients found in recipe")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 2: No ingredients found to standardize"]
            }
        
        logger.info(f"Processing {len(current_ingredients)} ingredients for recipe: {recipe_title}")
        
        # Standardize ingredients using Claude
        standardized_ingredients, changes_made = standardize_ingredients_with_claude(
            current_ingredients, recipe_title, cuisine_type
        )
        
        # Update the recipe JSON with standardized ingredients
        recipe_data['ingredients']['L'] = [{'S': ing} for ing in standardized_ingredients]
        
        # Create processing note with specific changes
        if changes_made and changes_made != "No ingredients required standardization":
            processing_note = f"Step 2: Standardized ingredients: {changes_made}"
        else:
            processing_note = f"Step 2: {changes_made}"
        
        # Return updated recipe with processing notes
        return {
            'statusCode': 200,
            'body': json.dumps(recipe_data),
            'processingNotes': previous_notes + [processing_note]
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'Invalid JSON: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 2: JSON parsing error - {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 2: Internal server error - {str(e)}"]
        }
