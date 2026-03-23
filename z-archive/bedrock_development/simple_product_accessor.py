import json
import boto3
import logging
from decimal import Decimal
from functools import lru_cache
from botocore.config import Config
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Custom JSON encoder to handle DynamoDB Decimal types
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            # Convert Decimal to int if it's a whole number, otherwise to float
            if obj % 1 == 0:
                return int(obj)
            else:
                return float(obj)
        return super(DecimalEncoder, self).default(obj)

def safe_json_dumps(obj, **kwargs):
    """Safely serialize objects that may contain Decimal values."""
    return json.dumps(obj, cls=DecimalEncoder, **kwargs)

# Configuration - EXACT SAME AS YOUR EXISTING LAMBDA
CONFIG = {
    'ROLE_ARN': "arn:aws:iam::970547358447:role/IsengardAccount-DynamoDBAccess",
    'TABLE_NAME': "AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev",
    'REGION': "us-west-1"
}

# AWS Configuration - EXACT SAME AS YOUR EXISTING LAMBDA
config_us_west_1 = Config(
    region_name=CONFIG['REGION'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 10, 'mode': 'standard'}
)

# Initialize AWS clients - EXACT SAME AS YOUR EXISTING LAMBDA
sts_client = boto3.client("sts", config=config_us_west_1)

def assume_cross_account_role():
    """EXACT SAME FUNCTION FROM YOUR EXISTING LAMBDA"""
    try:
        response = sts_client.assume_role(
            RoleArn=CONFIG['ROLE_ARN'],
            RoleSessionName="BedrockAgentProductAccess"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise

@lru_cache(maxsize=1)
def get_affiliate_products_cached(access_key, secret_key, session_token):
    """Get affiliate products using your existing database setup"""
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
        
        # Filter for products (same logic as Step 5)
        filtered_items = [item for item in items 
                         if item.get('linkLocation') and 
                         'products' in [loc.lower() for loc in item.get('linkLocation', [])]]
                
        logger.info(f"Retrieved {len(filtered_items)} affiliate products")
        return filtered_items
    
    except ClientError as e:
        logger.error(f"Error retrieving affiliate products from DynamoDB: {e}")
        raise

def get_product_by_id(product_id, credentials):
    """Get a specific affiliate product by ID using your existing database setup"""
    dynamodb = boto3.resource(
        "dynamodb",
        config=config_us_west_1,
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken']
    )
    table = dynamodb.Table(CONFIG['TABLE_NAME'])
    
    try:
        response = table.get_item(Key={'id': product_id})
        item = response.get('Item')
        
        if item:
            logger.info(f"Retrieved product: {product_id}")
            return item
        else:
            logger.warning(f"Product not found: {product_id}")
            return None
            
    except ClientError as e:
        logger.error(f"Error retrieving product {product_id}: {e}")
        raise

def search_products_by_category(category, credentials):
    """Search affiliate products by category using your existing database setup"""
    dynamodb = boto3.resource(
        "dynamodb",
        config=config_us_west_1,
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken']
    )
    table = dynamodb.Table(CONFIG['TABLE_NAME'])
    
    try:
        response = table.scan(
            FilterExpression="category = :category",
            ExpressionAttributeValues={":category": category}
        )
        
        items = response.get('Items', [])
        # Filter for products
        filtered_items = [item for item in items 
                         if item.get('linkLocation') and 
                         'products' in [loc.lower() for loc in item.get('linkLocation', [])]]
        
        logger.info(f"Found {len(filtered_items)} products in category: {category}")
        return filtered_items
        
    except ClientError as e:
        logger.error(f"Error searching products by category: {e}")
        raise

def parse_parameters(parameters):
    """Parse parameters from Bedrock agent format to dictionary."""
    if isinstance(parameters, dict):
        return parameters
    
    if isinstance(parameters, list):
        # Convert list of parameter objects to dictionary
        param_dict = {}
        for param in parameters:
            if isinstance(param, dict) and 'name' in param and 'value' in param:
                param_dict[param['name']] = param['value']
        return param_dict
    
    return {}

def lambda_handler(event, context):
    """Simple handler that just returns database data to Bedrock agent - NO LLM CALLS"""
    try:
        logger.info(f"Received event: {json.dumps(event, default=str)}")
        
        # Parse the action from Bedrock agent
        action = event.get('actionGroup', '')
        function_name = event.get('function', '')
        parameters = event.get('parameters', {})
        
        # Parse parameters properly
        parsed_params = parse_parameters(parameters)
        
        logger.info(f"Action: {action}, Function: {function_name}, Parameters: {parsed_params}")
        
        # Assume cross-account role - EXACT SAME AS YOUR EXISTING LAMBDA
        credentials = assume_cross_account_role()
        
        if function_name == 'get_all_affiliate_products':
            # Use your existing cached function
            result = get_affiliate_products_cached(
                credentials['AccessKeyId'],
                credentials['SecretAccessKey'],
                credentials['SessionToken']
            )
            
        elif function_name == 'get_product_by_id':
            product_id = parsed_params.get('product_id')
            if not product_id:
                raise ValueError("product_id parameter is required")
            result = get_product_by_id(product_id, credentials)
            
        elif function_name == 'search_products_by_category':
            category = parsed_params.get('category')
            if not category:
                raise ValueError("category parameter is required")
            result = search_products_by_category(category, credentials)
            
        else:
            raise ValueError(f"Unknown function: {function_name}")
        
        # Return in Bedrock agent format
        response_body = safe_json_dumps(result)
        
        return {
            'response': {
                'actionGroup': action,
                'function': function_name,
                'functionResponse': {
                    'responseBody': {
                        'TEXT': {
                            'body': response_body
                        }
                    }
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {e}")
        return {
            'response': {
                'actionGroup': action,
                'function': function_name,
                'functionResponse': {
                    'responseBody': {
                        'TEXT': {
                            'body': safe_json_dumps({'error': str(e)})
                        }
                    }
                }
            }
        }
