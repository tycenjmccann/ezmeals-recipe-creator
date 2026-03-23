import json
import boto3
import logging
from botocore.config import Config
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
CONFIG = {
    'ROLE_ARN': "arn:aws:iam::970547358447:role/IsengardAccount-DynamoDBAccess",
    'TABLE_NAME': "AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev",
    'REGION': "us-west-1"
}

# AWS Configuration
config = Config(
    region_name=CONFIG['REGION'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 10, 'mode': 'standard'}
)

# Initialize AWS clients
sts_client = boto3.client("sts", config=config)

def assume_cross_account_role():
    """Assumes a role in the EZMeals AWS account and returns temporary credentials."""
    try:
        response = sts_client.assume_role(
            RoleArn=CONFIG['ROLE_ARN'],
            RoleSessionName="BedrockAgentProductAccess"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise

def get_all_affiliate_products():
    """Get all affiliate products from AffiliateProduct table."""
    credentials = assume_cross_account_role()
    
    dynamodb = boto3.resource(
        "dynamodb",
        config=config,
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken']
    )
    
    table = dynamodb.Table(CONFIG['TABLE_NAME'])
    
    try:
        # Scan for products with linkLocation containing 'products'
        response = table.scan()
        items = response.get('Items', [])
        
        # Filter for products (same logic as Step 5)
        filtered_items = [item for item in items 
                         if item.get('linkLocation') and 
                         'products' in [loc.lower() for loc in item.get('linkLocation', [])]]
        
        logger.info(f"Retrieved {len(filtered_items)} affiliate products")
        return filtered_items
        
    except ClientError as e:
        logger.error(f"Error retrieving affiliate products: {e}")
        raise

def get_product_by_id(product_id):
    """Get a specific affiliate product by ID."""
    credentials = assume_cross_account_role()
    
    dynamodb = boto3.resource(
        "dynamodb",
        config=config,
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

def search_products_by_category(category):
    """Search affiliate products by category."""
    credentials = assume_cross_account_role()
    
    dynamodb = boto3.resource(
        "dynamodb",
        config=config,
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
    """Main Lambda handler for affiliate product operations."""
    try:
        logger.info(f"Received event: {json.dumps(event, default=str)}")
        
        # Parse the action from Bedrock agent
        action = event.get('actionGroup', '')
        function_name = event.get('function', '')
        parameters = event.get('parameters', {})
        
        # Parse parameters properly
        parsed_params = parse_parameters(parameters)
        
        logger.info(f"Action: {action}, Function: {function_name}, Parameters: {parsed_params}")
        
        if function_name == 'get_all_affiliate_products':
            result = get_all_affiliate_products()
            
        elif function_name == 'get_product_by_id':
            product_id = parsed_params.get('product_id')
            if not product_id:
                raise ValueError("product_id parameter is required")
            result = get_product_by_id(product_id)
            
        elif function_name == 'search_products_by_category':
            category = parsed_params.get('category')
            if not category:
                raise ValueError("category parameter is required")
            result = search_products_by_category(category)
            
        else:
            raise ValueError(f"Unknown function: {function_name}")
        
        return {
            'statusCode': 200,
            'body': {
                'TEXT': {
                    'body': json.dumps(result, default=str)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {e}")
        return {
            'statusCode': 500,
            'body': {
                'TEXT': {
                    'body': json.dumps({'error': str(e)})
                }
            }
        }
