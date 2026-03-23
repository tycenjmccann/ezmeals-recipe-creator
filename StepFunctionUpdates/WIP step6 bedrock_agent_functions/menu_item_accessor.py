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
    'TABLE_NAME': "MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev",
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
            RoleSessionName="BedrockAgentMenuItemAccess"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise

def get_side_dishes():
    """Get all side dishes from MenuItemData table."""
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
        # Scan for side dishes
        response = table.scan(
            FilterExpression="dishType = :dishType",
            ExpressionAttributeValues={":dishType": "side"}
        )
        
        items = response.get('Items', [])
        logger.info(f"Retrieved {len(items)} side dishes")
        return items
        
    except ClientError as e:
        logger.error(f"Error retrieving side dishes: {e}")
        raise

def get_menu_item_by_id(item_id):
    """Get a specific menu item by ID."""
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
        response = table.get_item(Key={'id': item_id})
        item = response.get('Item')
        
        if item:
            logger.info(f"Retrieved menu item: {item_id}")
            return item
        else:
            logger.warning(f"Menu item not found: {item_id}")
            return None
            
    except ClientError as e:
        logger.error(f"Error retrieving menu item {item_id}: {e}")
        raise

def search_menu_items(dish_type=None, cuisine_type=None):
    """Search menu items by dish type and/or cuisine type."""
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
        # Build filter expression
        filter_expressions = []
        expression_values = {}
        
        if dish_type:
            filter_expressions.append("dishType = :dishType")
            expression_values[":dishType"] = dish_type
            
        if cuisine_type:
            filter_expressions.append("cuisineType = :cuisineType")
            expression_values[":cuisineType"] = cuisine_type
        
        if filter_expressions:
            response = table.scan(
                FilterExpression=" AND ".join(filter_expressions),
                ExpressionAttributeValues=expression_values
            )
        else:
            response = table.scan()
        
        items = response.get('Items', [])
        logger.info(f"Found {len(items)} menu items matching criteria")
        return items
        
    except ClientError as e:
        logger.error(f"Error searching menu items: {e}")
        raise

def lambda_handler(event, context):
    """Main Lambda handler for menu item operations."""
    try:
        # Parse the action from Bedrock agent
        action = event.get('actionGroup', '')
        function_name = event.get('function', '')
        parameters = event.get('parameters', {})
        
        logger.info(f"Action: {action}, Function: {function_name}, Parameters: {parameters}")
        
        if function_name == 'get_side_dishes':
            result = get_side_dishes()
            
        elif function_name == 'get_menu_item_by_id':
            item_id = parameters.get('item_id')
            if not item_id:
                raise ValueError("item_id parameter is required")
            result = get_menu_item_by_id(item_id)
            
        elif function_name == 'search_menu_items':
            dish_type = parameters.get('dish_type')
            cuisine_type = parameters.get('cuisine_type')
            result = search_menu_items(dish_type, cuisine_type)
            
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
