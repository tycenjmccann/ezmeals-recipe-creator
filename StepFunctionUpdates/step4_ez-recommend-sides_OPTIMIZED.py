import json
import boto3
import logging
from decimal import Decimal
from botocore.config import Config
from botocore.exceptions import ClientError
from typing import Dict, List, Any

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
CONFIG = {
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
    retries={'max_attempts': 2, 'mode': 'adaptive'}
)

config_us_west_2 = Config(
    region_name=CONFIG['REGIONS']['BEDROCK'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 2, 'mode': 'adaptive'}
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

# Custom JSON encoder to handle Decimal objects from DynamoDB
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)

def safe_json_dumps(obj, **kwargs):
    """JSON dumps that handles Decimal objects from DynamoDB"""
    return json.dumps(obj, cls=DecimalEncoder, **kwargs)

def get_side_dishes_from_database():
    """Fetch side dishes from the MenuItemData table"""
    try:
        # Assume cross-account role
        assumed_role = sts_client.assume_role(
            RoleArn=CONFIG['ROLE_ARN'],
            RoleSessionName='SideRecommendation'
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
        
        # Scan for side dishes (dishType = "side")
        response = dynamodb.scan(
            TableName=CONFIG['TABLE_NAME'],
            FilterExpression='dishType = :dishType',
            ExpressionAttributeValues={':dishType': {'S': 'side'}}
        )
        
        side_dishes = []
        for item in response['Items']:
            side_dish = {
                'id': item.get('id', {}).get('S', ''),
                'title': item.get('title', {}).get('S', ''),
                'cuisineType': item.get('cuisineType', {}).get('S', ''),
                'description': item.get('description', {}).get('S', ''),
                'ingredients': []
            }
            
            # Extract ingredients if available
            ingredients_list = item.get('ingredients', {}).get('L', [])
            for ing in ingredients_list:
                if 'S' in ing:
                    side_dish['ingredients'].append(ing['S'])
            
            side_dishes.append(side_dish)
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = dynamodb.scan(
                TableName=CONFIG['TABLE_NAME'],
                FilterExpression='dishType = :dishType',
                ExpressionAttributeValues={':dishType': {'S': 'side'}},
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            
            for item in response['Items']:
                side_dish = {
                    'id': item.get('id', {}).get('S', ''),
                    'title': item.get('title', {}).get('S', ''),
                    'cuisineType': item.get('cuisineType', {}).get('S', ''),
                    'description': item.get('description', {}).get('S', ''),
                    'ingredients': []
                }
                
                # Extract ingredients if available
                ingredients_list = item.get('ingredients', {}).get('L', [])
                for ing in ingredients_list:
                    if 'S' in ing:
                        side_dish['ingredients'].append(ing['S'])
                
                side_dishes.append(side_dish)
        
        logger.info(f"Retrieved {len(side_dishes)} side dishes from database")
        return side_dishes
        
    except Exception as e:
        logger.error(f"Error fetching side dishes: {e}")
        return []

def get_side_dish_names(side_dish_ids: List[str], all_sides: List[Dict]) -> List[str]:
    """Get the actual names of side dishes from their IDs"""
    names = []
    id_to_name = {side['id']: side['title'] for side in all_sides}
    
    for side_id in side_dish_ids:
        name = id_to_name.get(side_id, f"Unknown Side ({side_id})")
        names.append(name)
    
    return names

def call_bedrock_api(conversation, max_retries=3):
    """Call Bedrock API with retry logic using new model fallback system"""
    return invoke_claude(
        conversation,
        {"maxTokens": 4096, "temperature": 0.7}
    )

def recommend_sides_with_claude(recipe_context: Dict, side_dishes: List[Dict]) -> List[str]:
    """Use Claude to recommend appropriate side dishes"""
    try:
        # Create context-optimized prompt with only essential recipe info
        recipe_summary = {
            'title': recipe_context.get('title', 'Unknown'),
            'cuisineType': recipe_context.get('cuisineType', 'Unknown'),
            'description': recipe_context.get('description', ''),
            'main_ingredients': recipe_context.get('ingredients', [])[:10]  # Limit to first 10
        }
        
        # Create simplified side dishes list for Claude
        sides_summary = []
        for side in side_dishes[:50]:  # Limit to 50 sides to reduce context
            sides_summary.append({
                'id': side['id'],
                'title': side['title'],
                'cuisineType': side['cuisineType'],
                'description': side['description'][:100]  # Truncate descriptions
            })
        
        prompt = f"""
Recommend 3-4 appropriate side dishes for this main dish recipe.

Main Dish:
{safe_json_dumps(recipe_summary, indent=2)}

Available Side Dishes:
{safe_json_dumps(sides_summary, indent=2)}

Consider:
1. Cuisine compatibility (match or complement the cuisine type)
2. Flavor profiles that work well together
3. Nutritional balance and variety
4. Traditional pairings

Return ONLY a JSON array of the recommended side dish IDs:
["side-id-1", "side-id-2", "side-id-3"]

Choose sides that would genuinely complement this main dish.
"""
        
        conversation = [{"role": "user", "content": [{"text": prompt}]}]
        
        logger.info("Calling Claude for side dish recommendations")
        response = call_bedrock_api(conversation)
        response_text = response['output']['message']['content'][0]['text']
        
        # Extract JSON array from response
        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1
        
        if json_start == -1 or json_end == 0:
            logger.warning("No JSON array found in Claude response")
            return []
        
        json_str = response_text[json_start:json_end]
        recommended_ids = json.loads(json_str)
        
        # Validate that IDs exist in our side dishes
        valid_ids = [side['id'] for side in side_dishes]
        filtered_ids = [id for id in recommended_ids if id in valid_ids]
        
        logger.info(f"Claude recommended {len(filtered_ids)} valid side dishes")
        return filtered_ids
        
    except Exception as e:
        logger.error(f"Error getting side recommendations from Claude: {e}")
        return []

def lambda_handler(event, context):
    """
    AWS Lambda handler for recommending side dishes for main dishes.
    """
    logger.info("Starting side dish recommendation")
    
    try:
        # Extract data from the event
        step_output = event.get('stepOutput', {})
        recipe_json_str = step_output.get('body', '{}')
        previous_notes = step_output.get('processingNotes', [])
        
        # Parse the recipe JSON
        recipe_data = json.loads(recipe_json_str)
        
        # Check if this is a main dish
        dish_type = recipe_data.get('dishType', {}).get('S', '')
        is_primary = recipe_data.get('primary', {}).get('BOOL', False)
        
        if dish_type != 'main' and not is_primary:
            logger.info("Recipe is not a main dish, skipping side recommendations")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 4: Side dish detected - skipped side recommendations"]
            }
        
        # Extract recipe context for Claude (CONTEXT OPTIMIZATION)
        recipe_context = {
            'title': recipe_data.get('title', {}).get('S', ''),
            'cuisineType': recipe_data.get('cuisineType', {}).get('S', ''),
            'description': recipe_data.get('description', {}).get('S', ''),
            'ingredients': [item.get('S', '') for item in recipe_data.get('ingredients', {}).get('L', [])]
        }
        
        # Get side dishes from database
        side_dishes = get_side_dishes_from_database()
        if not side_dishes:
            logger.warning("No side dishes available in database")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 4: No side dishes available in database"]
            }
        
        # Get recommendations from Claude
        recommended_side_ids = recommend_sides_with_claude(recipe_context, side_dishes)
        
        if not recommended_side_ids:
            logger.info("No side dish recommendations generated")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 4: No valid side dish recommendations found"]
            }
        
        # Update recipe with recommended sides
        recommended_sides_dynamodb = [{'S': side_id} for side_id in recommended_side_ids]
        recipe_data['recommendedSides'] = {'L': recommended_sides_dynamodb}
        
        # Get side dish names for processing notes
        side_names = get_side_dish_names(recommended_side_ids, side_dishes)
        side_names_str = ", ".join(side_names)
        
        # Create processing note with actual side dish names
        processing_note = f"Step 4: Recommended {len(recommended_side_ids)} side dishes: {side_names_str}"
        
        # Return updated recipe with processing notes
        return {
            'statusCode': 200,
            'body': safe_json_dumps(recipe_data),
            'processingNotes': previous_notes + [processing_note]
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'Invalid JSON: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 4: JSON parsing error - {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 4: Internal server error - {str(e)}"]
        }
