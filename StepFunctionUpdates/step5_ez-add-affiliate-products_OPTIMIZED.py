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
    'TABLE_NAME': "AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev",
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

def get_affiliate_products_from_database():
    """Fetch all affiliate products from the database"""
    try:
        # Assume cross-account role
        assumed_role = sts_client.assume_role(
            RoleArn=CONFIG['ROLE_ARN'],
            RoleSessionName='AffiliateProductIntegration'
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
        
        # Scan the AffiliateProduct table
        response = dynamodb.scan(TableName=CONFIG['TABLE_NAME'])
        
        products = []
        for item in response['Items']:
            product = {
                'id': item.get('id', {}).get('S', ''),
                'product_name': item.get('product_name', {}).get('S', ''),
                'category': item.get('category', {}).get('S', ''),
                'description': item.get('description', {}).get('S', ''),
                'price': item.get('price', {}).get('S', ''),
                'affiliate_link': item.get('affiliate_link', {}).get('S', ''),
                'image_url': item.get('image_url', {}).get('S', ''),
                'keywords': item.get('keywords', {}).get('S', '')
            }
            products.append(product)
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = dynamodb.scan(
                TableName=CONFIG['TABLE_NAME'],
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            
            for item in response['Items']:
                product = {
                    'id': item.get('id', {}).get('S', ''),
                    'product_name': item.get('product_name', {}).get('S', ''),
                    'category': item.get('category', {}).get('S', ''),
                    'description': item.get('description', {}).get('S', ''),
                    'price': item.get('price', {}).get('S', ''),
                    'affiliate_link': item.get('affiliate_link', {}).get('S', ''),
                    'image_url': item.get('image_url', {}).get('S', ''),
                    'keywords': item.get('keywords', {}).get('S', '')
                }
                products.append(product)
        
        logger.info(f"Retrieved {len(products)} affiliate products from database")
        return products
        
    except Exception as e:
        logger.error(f"Error fetching affiliate products: {e}")
        return []

def get_product_names(product_ids: List[str], all_products: List[Dict]) -> List[str]:
    """Get the actual names of products from their IDs"""
    names = []
    id_to_name = {product['id']: product['product_name'] for product in all_products}
    
    for product_id in product_ids:
        name = id_to_name.get(product_id, f"Unknown Product ({product_id})")
        names.append(name)
    
    return names

def call_bedrock_api(conversation, max_retries=3):
    """Call Bedrock API with retry logic using new model fallback system"""
    return invoke_claude(
        conversation,
        {"maxTokens": 4096, "temperature": 0.7}
    )

def recommend_products_with_claude(recipe_context: Dict, products: List[Dict]) -> List[str]:
    """Use Claude to recommend relevant affiliate products"""
    try:
        # Create context-optimized prompt with only essential recipe info
        recipe_summary = {
            'title': recipe_context.get('title', 'Unknown'),
            'cuisineType': recipe_context.get('cuisineType', 'Unknown'),
            'description': recipe_context.get('description', ''),
            'ingredients': recipe_context.get('ingredients', [])[:15],  # Limit ingredients
            'instructions': recipe_context.get('instructions', [])[:10]  # Limit instructions
        }
        
        prompt = f"""
Recommend 2-4 relevant affiliate products for this recipe. Focus ONLY on kitchen tools, equipment, and non-consumable items that would genuinely help with this recipe.

Recipe:
{safe_json_dumps(recipe_summary, indent=2)}

Available Products:
{safe_json_dumps(products, indent=2)}

IMPORTANT RULES:
1. DO NOT recommend consumable items (spices, oils, food ingredients)
2. ONLY recommend reusable kitchen tools and equipment
3. Consider what tools/equipment would actually be useful for this recipe
4. Focus on items that enhance the cooking process
5. Apply "would they actually use this?" criteria

Return ONLY a JSON array of the recommended product IDs:
["product-id-1", "product-id-2", "product-id-3"]

Choose products that would genuinely be useful for making this recipe.
"""
        
        conversation = [{"role": "user", "content": [{"text": prompt}]}]
        
        logger.info("Calling Claude for affiliate product recommendations")
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
        
        # Validate that IDs exist in our products
        valid_ids = [product['id'] for product in products]
        filtered_ids = [id for id in recommended_ids if id in valid_ids]
        
        logger.info(f"Claude recommended {len(filtered_ids)} valid affiliate products")
        return filtered_ids
        
    except Exception as e:
        logger.error(f"Error getting product recommendations from Claude: {e}")
        return []

def lambda_handler(event, context):
    """
    AWS Lambda handler for adding affiliate products to recipes.
    """
    logger.info("Starting affiliate product integration")
    
    try:
        # Extract data from the event
        step_output = event.get('stepOutput', {})
        recipe_json_str = step_output.get('body', '{}')
        previous_notes = step_output.get('processingNotes', [])
        
        # Parse the recipe JSON
        recipe_data = json.loads(recipe_json_str)
        
        # Extract recipe context for Claude (CONTEXT OPTIMIZATION)
        recipe_context = {
            'title': recipe_data.get('title', {}).get('S', ''),
            'cuisineType': recipe_data.get('cuisineType', {}).get('S', ''),
            'description': recipe_data.get('description', {}).get('S', ''),
            'ingredients': [item.get('S', '') for item in recipe_data.get('ingredients', {}).get('L', [])],
            'instructions': [item.get('S', '') for item in recipe_data.get('instructions', {}).get('L', [])]
        }
        
        # Get affiliate products from database
        products = get_affiliate_products_from_database()
        if not products:
            logger.warning("No affiliate products available in database")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 5: No affiliate products available in database"]
            }
        
        # Get recommendations from Claude
        recommended_product_ids = recommend_products_with_claude(recipe_context, products)
        
        if not recommended_product_ids:
            logger.info("No affiliate product recommendations generated")
            return {
                'statusCode': 200,
                'body': recipe_json_str,
                'processingNotes': previous_notes + ["Step 5: No valid affiliate products found for this recipe"]
            }
        
        # Update recipe with recommended products
        recommended_products_dynamodb = [{'S': product_id} for product_id in recommended_product_ids]
        recipe_data['products'] = {'L': recommended_products_dynamodb}
        
        # Get product names for processing notes
        product_names = get_product_names(recommended_product_ids, products)
        product_names_str = ", ".join(product_names)
        
        # Create processing note with actual product names
        processing_note = f"Step 5: Added {len(recommended_product_ids)} affiliate products: {product_names_str}"
        
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
            'processingNotes': previous_notes + [f"Step 5: JSON parsing error - {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'}),
            'processingNotes': previous_notes + [f"Step 5: Internal server error - {str(e)}"]
        }
