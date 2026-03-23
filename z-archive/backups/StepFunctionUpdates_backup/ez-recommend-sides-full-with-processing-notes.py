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

# Claude prompt template
CLAUDE_PROMPT_TEMPLATE = """
You are a culinary expert tasked with recommending side dishes that would pair well with a main dish.

You are provided with:
1. The original recipe text for a main dish
2. The structured JSON format of that main dish
3. A list of available side dishes from our menu

Your role is to analyze the main dish and recommend 3-5 side dishes that would complement it well based on:
- Cuisine compatibility (e.g., Asian sides with Asian mains)
- Flavor profiles that complement each other
- Nutritional balance
- Cooking method compatibility
- Traditional pairings

Here are the available side dishes to choose from:
{available_sides}

Instructions:
- Update the existing_json "recommendedSides" list by adding the side dish IDs
- Only include side dish IDs from the provided list
- Recommend 3-5 sides that would pair well with this main dish
- Consider cuisine type, flavors, and traditional pairings
- Do not modify any other JSON attributes
- Return only the updated JSON, no explanations

Main dish recipe:
{recipe_text}

Existing JSON:
{existing_json}
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

def get_side_dishes(credentials):
    """Wrapper function to call the cached version with unpacked credentials"""
    return get_side_dishes_cached(
        credentials['AccessKeyId'],
        credentials['SecretAccessKey'],
        credentials['SessionToken']
    )

def get_claude_prompt(recipe_text, existing_json, side_dishes):
    """Generate formatted prompt for Claude"""
    sides_for_prompt = [{
        'id': side.get('id'),
        'title': side.get('title'),
        'description': side.get('description'),
        'cuisineType': side.get('cuisineType'),
        'ingredients': side.get('ingredients', [])[:5] if side.get('ingredients') else [],  # First 5 ingredients for context
        'vegetarian': side.get('vegetarian'),
        'glutenFree': side.get('glutenFree')
    } for side in side_dishes]
    
    return CLAUDE_PROMPT_TEMPLATE.format(
        recipe_text=recipe_text,
        existing_json=existing_json,
        available_sides=json.dumps(sides_for_prompt, indent=2)
    )

def extract_json(response_text):
    """Extract and clean JSON from Claude's response"""
    # Extract JSON part (everything between first { and last })
    json_match = re.search(r'({.*})', response_text, re.DOTALL)
    if not json_match:
        raise ValueError("No JSON found in response")
    
    json_str = json_match.group(1)
    
    # Ensure we have all closing braces
    open_count = json_str.count('{')
    close_count = json_str.count('}')
    if open_count > close_count:
        json_str += '}' * (open_count - close_count)
    
    return json_str

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

def validate_response(response_json, original_json):
    """Validate that the Claude response maintains required structure"""
    if not isinstance(response_json, dict):
        raise ValueError("Response must be a dictionary")
    
    # Verify all original keys are present
    for key in original_json:
        if key not in response_json:
            raise ValueError(f"Response missing original key: {key}")
            
    return True

def lambda_handler(event, context):
    """Main Lambda handler function with full side recommendation logic and processing notes."""
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
            logger.error(f"[{request_id}] Error processing input: {e}")
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
        
        # Assume cross-account role
        credentials = assume_cross_account_role()
        if not credentials:
            processing_notes = add_processing_note(processing_notes, "Step 4", "Could not assume cross-account role")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not assume cross-account role'}),
                'processingNotes': processing_notes
            }

        # Get available side dishes
        side_dishes = get_side_dishes(credentials)
        if not side_dishes:
            logger.warning(f"[{request_id}] No side dishes found, proceeding with empty recommendedSides")
            processing_notes = add_processing_note(processing_notes, "Step 4", "No side dishes found in database")
            return {
                'statusCode': 200,
                'body': json.dumps(step_output_data),
                'processingNotes': processing_notes
            }

        logger.info(f"[{request_id}] Retrieved {len(side_dishes)} available side dishes")

        # Prepare and send prompt to Claude
        prompt = get_claude_prompt(recipe_text, json.dumps(step_output_data, indent=2), side_dishes)
        conversation = [{"role": "user", "content": [{"text": prompt}]}]
        
        try:
            claude_response = call_bedrock_api(conversation)
            response_text = claude_response["output"]["message"]["content"][0]["text"]
            
            # Log Claude's complete response
            logger.info(f"[{request_id}] Claude's complete response:")
            logger.info(response_text)
            
            # Extract and parse JSON
            try:
                json_str = extract_json(response_text)
                logger.info(f"[{request_id}] Extracted JSON string:")
                logger.info(json_str)
                
                updated_body = json.loads(json_str)
                logger.info(f"[{request_id}] Parsed JSON object:")
                logger.info(json.dumps(updated_body, indent=2))
                
                # Validate response
                validate_response(updated_body, step_output_data)
                logger.info(f"[{request_id}] Response validation successful")
                
                # Validate that recommended sides exist in our database
                sides_recommended = 0
                if 'recommendedSides' in updated_body:
                    valid_side_ids = {s.get('id') for s in side_dishes}
                    
                    # Extract side IDs from DynamoDB format
                    current_sides = [item['S'] for item in updated_body['recommendedSides']['L']]
                    logger.info(f"[{request_id}] Current recommended side IDs: {current_sides}")
                    
                    # Filter valid sides
                    valid_sides = [
                        sid for sid in current_sides 
                        if sid in valid_side_ids
                    ]
                    logger.info(f"[{request_id}] Valid recommended side IDs: {valid_sides}")
                    
                    # Update in DynamoDB format
                    updated_body['recommendedSides'] = {
                        'L': [{'S': sid} for sid in valid_sides]
                    }
                    
                    sides_recommended = len(valid_sides)
                
                # Add processing notes based on results
                if sides_recommended > 0:
                    processing_notes = add_processing_note(processing_notes, "Step 4", f"Recommended {sides_recommended} side dishes for main dish")
                else:
                    processing_notes = add_processing_note(processing_notes, "Step 4", "No suitable side dish recommendations found")
                
                logger.info(f"[{request_id}] Successfully processed side recommendations")
                return {
                    'statusCode': 200,
                    'body': json.dumps(updated_body),
                    'processingNotes': processing_notes
                }
                
            except (json.JSONDecodeError, ValueError) as je:
                logger.error(f"[{request_id}] JSON parsing error: {je}")
                logger.error(f"[{request_id}] Problematic JSON string:")
                logger.error(json_str if 'json_str' in locals() else "No JSON string extracted")
                processing_notes = add_processing_note(processing_notes, "Step 4", f"Claude response parsing error: {str(je)}")
                raise ValueError("Unable to parse Claude's response as valid JSON")
            
        except (ClientError, ValueError, Exception) as e:
            logger.error(f"[{request_id}] Error processing with Claude: {e}")
            if 'response_text' in locals():
                logger.error(f"[{request_id}] Claude's raw response:")
                logger.error(response_text)
            processing_notes = add_processing_note(processing_notes, "Step 4", f"Claude processing error: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Error processing with Claude',
                    'details': str(e)
                }),
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
