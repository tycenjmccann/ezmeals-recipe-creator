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
    'TABLE_NAME': "MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev",
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

# OPTIMIZED Claude prompt template - requests only side dish IDs
CLAUDE_PROMPT_TEMPLATE = """
You are a culinary expert tasked with recommending side dishes that would pair well with a main dish.

Main Dish Context:
Title: {title}
Cuisine Type: {cuisine_type}
Description: {description}

Main Dish Ingredients:
{ingredients}

Main Dish Instructions:
{instructions}

Available Side Dishes:
{available_sides}

Your task:
- Analyze the main dish and recommend 3-5 side dishes that would complement it well
- Consider cuisine compatibility (e.g., Asian sides with Asian mains)
- Consider flavor profiles that complement each other
- Consider nutritional balance and traditional pairings
- Only recommend side dish IDs from the provided list

Return ONLY a JSON array of side dish IDs that would pair well:
[
    "side-dish-id-1",
    "side-dish-id-2",
    "side-dish-id-3"
]

Rules:
- Return only side dish IDs from the provided list
- Maximum 5 side dishes
- Only include sides that genuinely complement this main dish
- Return empty array [] if no suitable pairings found
- No explanations, just the JSON array
"""

def extract_previous_processing_notes(event):
    """Extract processing notes from previous steps."""
    previous_notes = []
    step_output = event.get('stepOutput', {})
    
    if isinstance(step_output, dict) and 'processingNotes' in step_output:
        previous_notes = step_output['processingNotes']
    
    return previous_notes

def add_processing_note(previous_notes, step_name, note):
    """Add a new processing note to the accumulated list."""
    new_notes = previous_notes.copy()
    new_notes.append(f"{step_name}: {note}")
    return new_notes

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
            RoleSessionName="SideRecommendationAccessSession"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise

def extract_recipe_context(step_output_data):
    """Extract minimal context for side dish pairing."""
    return {
        'title': step_output_data.get('title', {}).get('S', 'Unknown Recipe'),
        'cuisine_type': step_output_data.get('cuisineType', {}).get('S', 'Unknown'),
        'description': step_output_data.get('description', {}).get('S', '')
    }

def extract_recipe_content(step_output_data):
    """Extract ingredients and instructions for side dish analysis."""
    # Extract ingredients
    ingredients = step_output_data.get('ingredients', {}).get('L', [])
    ingredients_list = [item.get('S', '') for item in ingredients if item.get('S', '').strip()]
    
    # Extract instructions
    instructions = step_output_data.get('instructions', {}).get('L', [])
    instructions_list = [item.get('S', '') for item in instructions if item.get('S', '').strip()]
    
    return ingredients_list, instructions_list

def get_filtered_side_dishes(credentials, recipe_context, ingredients_list):
    """Get side dishes filtered by relevance to the main dish."""
    # Get all side dishes (cached)
    all_sides = get_side_dishes_cached(
        credentials['AccessKeyId'],
        credentials['SecretAccessKey'],
        credentials['SessionToken']
    )
    
    if not all_sides:
        return []
    
    # Extract keywords from main dish for filtering
    main_dish_keywords = set()
    
    # Add ingredients keywords
    for ingredient in ingredients_list:
        words = re.findall(r'\b[A-Za-z]{3,}\b', ingredient.lower())
        main_dish_keywords.update(words)
    
    # Add cuisine type keywords
    if recipe_context['cuisine_type'] != 'Unknown':
        words = re.findall(r'\b[A-Za-z]{3,}\b', recipe_context['cuisine_type'].lower())
        main_dish_keywords.update(words)
    
    # Filter sides by cuisine compatibility and relevance
    relevant_sides = []
    for side in all_sides:
        side_cuisine = side.get('cuisineType', '').lower()
        main_cuisine = recipe_context['cuisine_type'].lower()
        
        # Include if same cuisine or complementary
        if (side_cuisine == main_cuisine or 
            main_cuisine in ['global cuisines', 'american'] or
            side_cuisine in ['global cuisines', 'american'] or
            any(keyword in side.get('title', '').lower() for keyword in main_dish_keywords if len(keyword) > 3)):
            relevant_sides.append(side)
    
    logger.info(f"Filtered side dishes: {len(relevant_sides)} relevant out of {len(all_sides)} total")
    return relevant_sides

@lru_cache(maxsize=1)
def get_side_dishes_cached(access_key, secret_key, session_token):
    """Cached version of side dishes fetch with pagination and filtering"""
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
            scan_kwargs = {
                'FilterExpression': 'dishType = :dishType',
                'ExpressionAttributeValues': {
                    ':dishType': 'side'
                }
            }
            if last_evaluated_key:
                scan_kwargs['ExclusiveStartKey'] = last_evaluated_key
                
            response = table.scan(**scan_kwargs)
            items.extend(response.get('Items', []))
            
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break
                
        logger.info(f"Retrieved {len(items)} side dishes")
        return items
    
    except ClientError as e:
        logger.error(f"Error retrieving side dishes from DynamoDB: {e}")
        raise

def create_claude_prompt(recipe_context, ingredients_list, instructions_list, side_dishes):
    """Generate optimized prompt for Claude with minimal context."""
    # Simplify side dishes for Claude
    sides_for_prompt = [{
        'id': side.get('id'),
        'title': side.get('title'),
        'description': side.get('description'),
        'cuisineType': side.get('cuisineType'),
        'ingredients': side.get('ingredients', [])[:3] if side.get('ingredients') else [],  # First 3 ingredients for context
        'vegetarian': side.get('vegetarian'),
        'glutenFree': side.get('glutenFree')
    } for side in side_dishes]
    
    return CLAUDE_PROMPT_TEMPLATE.format(
        title=recipe_context['title'],
        cuisine_type=recipe_context['cuisine_type'],
        description=recipe_context['description'],
        ingredients=json.dumps(ingredients_list, indent=2),
        instructions=json.dumps(instructions_list[:3], indent=2),  # First 3 instructions for context
        available_sides=json.dumps(sides_for_prompt, indent=2)
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

def extract_side_dish_ids(response_text):
    """Extract side dish IDs array from Claude's response."""
    logger.info(f"Raw Claude response length: {len(response_text)} characters")
    
    # Look for JSON array structure
    json_match = re.search(r'\[.*?\]', response_text, re.DOTALL)
    if not json_match:
        logger.error(f"No JSON array found in Claude response: {response_text}")
        raise ValueError("No side dish IDs array found in Claude's response")
    
    json_str = json_match.group(0)
    logger.info(f"Extracted JSON array: {json_str}")
    
    try:
        side_dish_ids = json.loads(json_str)
        if not isinstance(side_dish_ids, list):
            raise ValueError("Claude response must be a list of side dish IDs")
        
        logger.info(f"Successfully parsed {len(side_dish_ids)} side dish IDs")
        return side_dish_ids
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed. Extracted string: {json_str}")
        raise ValueError(f"Invalid JSON in Claude's response: {e}")

def validate_side_dish_ids(side_dish_ids, available_sides):
    """Validate that side dish IDs exist in our database."""
    if not isinstance(side_dish_ids, list):
        raise ValueError("Side dish IDs must be a list")
    
    valid_side_ids = {s.get('id') for s in available_sides}
    valid_ids = [sid for sid in side_dish_ids if sid in valid_side_ids]
    
    logger.info(f"Validation: {len(valid_ids)} valid out of {len(side_dish_ids)} suggested side dish IDs")
    return valid_ids

def merge_side_dish_ids(original_json, side_dish_ids, request_id):
    """Safely merge side dish IDs back into the original JSON."""
    
    logger.info(f"[{request_id}] Starting recommendedSides merge validation")
    
    # Validate original JSON structure
    if not isinstance(original_json, dict):
        raise ValueError(f"Original JSON must be a dictionary, got: {type(original_json)}")
    
    if 'recommendedSides' not in original_json:
        raise ValueError("Original JSON missing 'recommendedSides' field for merge")
    
    if not isinstance(original_json['recommendedSides'], dict) or 'L' not in original_json['recommendedSides']:
        raise ValueError("Original recommendedSides field has wrong structure")
    
    logger.info(f"[{request_id}] Original JSON structure validation passed")
    
    # Convert side dish IDs to DynamoDB format
    sides_dynamodb = {'L': [{'S': sid} for sid in side_dish_ids]}
    
    # Store original for comparison
    original_sides = original_json['recommendedSides']['L']
    logger.info(f"[{request_id}] Replacing {len(original_sides)} sides with {len(side_dish_ids)} new recommended sides")
    
    # Perform the merge
    original_json['recommendedSides'] = sides_dynamodb
    
    # Validate the merge worked correctly
    if len(original_json['recommendedSides']['L']) != len(side_dish_ids):
        raise ValueError("Merge validation failed - side dish count mismatch")
    
    logger.info(f"[{request_id}] Successfully merged {len(side_dish_ids)} recommended sides")
    logger.info(f"[{request_id}] RecommendedSides merge validation: All checks passed")
    
    return original_json

def lambda_handler(event, context):
    """Main Lambda handler with optimized context window usage and processing notes."""
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting side dish recommendation processing")
    
    # Extract previous processing notes
    processing_notes = extract_previous_processing_notes(event)
    logger.info(f"[{request_id}] Received {len(processing_notes)} previous processing notes")
    
    try:
        # Validate input
        recipe_text, step_output = validate_input(event)
        logger.info(f"[{request_id}] Input validation successful")
        
        # Parse step output to check if this is a main dish
        try:
            step_output_data = json.loads(step_output)
            logger.info(f"[{request_id}] Successfully parsed step output data")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"[{request_id}] JSON parsing failed: {e}")
            processing_notes = add_processing_note(processing_notes, "Step 4", f"JSON parsing error: {str(e)}")
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid input or JSON parsing error.'}),
                'processingNotes': processing_notes
            }
        
        # Check if this is a main dish
        dish_type = step_output_data.get('dishType', {}).get('S', '')
        logger.info(f"[{request_id}] Processing dish type: {dish_type}")
        
        if dish_type != 'main':
            logger.info(f"[{request_id}] Side dish detected, passing through unchanged")
            processing_notes = add_processing_note(processing_notes, "Step 4", "Side dish detected - skipped side recommendations")
            return {
                'statusCode': 200,
                'body': json.dumps(step_output_data),
                'processingNotes': processing_notes
            }
        
        logger.info(f"[{request_id}] Main dish detected, proceeding with side recommendations")
        processing_notes = add_processing_note(processing_notes, "Step 4", "Main dish detected - processing side recommendations")
        
        # Extract recipe context and content
        logger.info(f"[{request_id}] Extracting recipe context and content")
        recipe_context = extract_recipe_context(step_output_data)
        ingredients_list, instructions_list = extract_recipe_content(step_output_data)
        logger.info(f"[{request_id}] Processing recipe: {recipe_context['title']} with {len(ingredients_list)} ingredients")
        
        # Assume cross-account role
        logger.info(f"[{request_id}] Assuming cross-account role")
        credentials = assume_cross_account_role()
        
        # Get filtered side dishes
        logger.info(f"[{request_id}] Retrieving filtered side dishes")
        side_dishes = get_filtered_side_dishes(credentials, recipe_context, ingredients_list)
        
        if not side_dishes:
            logger.warning(f"[{request_id}] No relevant side dishes found")
            processing_notes = add_processing_note(processing_notes, "Step 4", "No relevant side dishes found for this main dish")
            return {
                'statusCode': 200,
                'body': json.dumps(step_output_data),
                'processingNotes': processing_notes
            }
        
        # Create optimized prompt
        logger.info(f"[{request_id}] Creating optimized Claude prompt")
        prompt = create_claude_prompt(recipe_context, ingredients_list, instructions_list, side_dishes)
        logger.info(f"[{request_id}] Prompt size: {len(prompt)} characters")
        
        # Get side dish IDs from Claude
        logger.info(f"[{request_id}] Invoking Claude for side dish recommendations")
        conversation = [{"role": "user", "content": [{"text": prompt}]}]
        claude_response = call_bedrock_api(conversation)
        response_text = claude_response["output"]["message"]["content"][0]["text"]
        
        # Extract and validate side dish IDs
        logger.info(f"[{request_id}] Extracting side dish IDs from response")
        side_dish_ids = extract_side_dish_ids(response_text)
        
        logger.info(f"[{request_id}] Validating side dish IDs")
        valid_side_ids = validate_side_dish_ids(side_dish_ids, side_dishes)
        
        # Add processing notes based on results
        if valid_side_ids:
            processing_notes = add_processing_note(processing_notes, "Step 4", f"Recommended {len(valid_side_ids)} side dishes for main dish")
        else:
            processing_notes = add_processing_note(processing_notes, "Step 4", "No suitable side dish recommendations found")
        
        # Merge back into original JSON
        logger.info(f"[{request_id}] Merging side dish IDs into original JSON")
        updated_json = merge_side_dish_ids(step_output_data, valid_side_ids, request_id)
        
        logger.info(f"[{request_id}] Successfully processed side dish recommendations")
        return {
            'statusCode': 200,
            'body': json.dumps(updated_json),
            'processingNotes': processing_notes
        }
        
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 4", f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Unexpected error',
                'details': str(e)
            }),
            'processingNotes': processing_notes
        }
