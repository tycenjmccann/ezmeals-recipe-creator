import json
import boto3
import logging
from botocore.config import Config
from botocore.exceptions import ClientError
from datetime import datetime

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration - using the same S3 setup as your Streamlit app
CONFIG = {
    'S3_BUCKET': 'menu-items-json',
    'S3_REGION': 'us-west-1',
    'LOCAL_BACKUP_PATH': '/tmp'  # Lambda temp directory
}

# AWS Configuration for S3
s3_config = Config(
    region_name=CONFIG['S3_REGION'],
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 10, 'mode': 'standard'}
)

# Initialize S3 client
s3_client = boto3.client('s3', config=s3_config)

def upload_recipe_to_s3(recipe_data, recipe_name):
    """Upload recipe JSON to S3 bucket."""
    try:
        # Clean recipe name for filename (same logic as Streamlit app)
        safe_filename = recipe_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        if not safe_filename.endswith('.json'):
            safe_filename += '.json'
        
        # Convert recipe data to JSON string
        if isinstance(recipe_data, dict):
            json_content = json.dumps(recipe_data, indent=2, default=str)
        else:
            json_content = str(recipe_data)
        
        # Upload to S3
        s3_client.put_object(
            Bucket=CONFIG['S3_BUCKET'],
            Key=safe_filename,
            Body=json_content,
            ContentType='application/json'
        )
        
        logger.info(f"Successfully uploaded {safe_filename} to S3 bucket {CONFIG['S3_BUCKET']}")
        
        return {
            'success': True,
            'filename': safe_filename,
            'bucket': CONFIG['S3_BUCKET'],
            'size': len(json_content),
            'message': f'Recipe "{recipe_name}" successfully uploaded to S3'
        }
        
    except ClientError as e:
        logger.error(f"Error uploading to S3: {e}")
        raise Exception(f"Failed to upload to S3: {str(e)}")

def list_recipes_in_s3():
    """List all recipe files in the S3 bucket."""
    try:
        response = s3_client.list_objects_v2(
            Bucket=CONFIG['S3_BUCKET'],
            MaxKeys=100
        )
        
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                files.append({
                    'filename': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'url': f"s3://{CONFIG['S3_BUCKET']}/{obj['Key']}"
                })
        
        logger.info(f"Found {len(files)} files in S3 bucket {CONFIG['S3_BUCKET']}")
        return files
        
    except ClientError as e:
        logger.error(f"Error listing S3 objects: {e}")
        raise Exception(f"Failed to list S3 objects: {str(e)}")

def get_recipe_from_s3(filename):
    """Get a specific recipe file from S3."""
    try:
        response = s3_client.get_object(
            Bucket=CONFIG['S3_BUCKET'],
            Key=filename
        )
        
        content = response['Body'].read().decode('utf-8')
        recipe_data = json.loads(content)
        
        logger.info(f"Successfully retrieved {filename} from S3")
        
        return {
            'filename': filename,
            'content': recipe_data,
            'size': response['ContentLength'],
            'last_modified': response['LastModified'].isoformat()
        }
        
    except ClientError as e:
        logger.error(f"Error getting file from S3: {e}")
        raise Exception(f"Failed to get file from S3: {str(e)}")
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON content: {e}")
        raise Exception(f"Invalid JSON content in file: {str(e)}")

def delete_recipe_from_s3(filename):
    """Delete a recipe file from S3."""
    try:
        s3_client.delete_object(
            Bucket=CONFIG['S3_BUCKET'],
            Key=filename
        )
        
        logger.info(f"Successfully deleted {filename} from S3")
        
        return {
            'success': True,
            'filename': filename,
            'message': f'Recipe file "{filename}" successfully deleted from S3'
        }
        
    except ClientError as e:
        logger.error(f"Error deleting from S3: {e}")
        raise Exception(f"Failed to delete from S3: {str(e)}")

def lambda_handler(event, context):
    """Main Lambda handler for S3 operations."""
    try:
        # Parse the action from Bedrock agent
        action = event.get('actionGroup', '')
        function_name = event.get('function', '')
        parameters = event.get('parameters', {})
        
        logger.info(f"Action: {action}, Function: {function_name}, Parameters: {parameters}")
        
        if function_name == 'upload_recipe_to_s3':
            recipe_data = parameters.get('recipe_data')
            recipe_name = parameters.get('recipe_name')
            
            if not recipe_data or not recipe_name:
                raise ValueError("recipe_data and recipe_name parameters are required")
            
            # Parse recipe_data if it's a string
            if isinstance(recipe_data, str):
                try:
                    recipe_data = json.loads(recipe_data)
                except json.JSONDecodeError:
                    # If it's not valid JSON, treat as plain text
                    pass
            
            result = upload_recipe_to_s3(recipe_data, recipe_name)
            
        elif function_name == 'list_recipes_in_s3':
            result = list_recipes_in_s3()
            
        elif function_name == 'get_recipe_from_s3':
            filename = parameters.get('filename')
            if not filename:
                raise ValueError("filename parameter is required")
            result = get_recipe_from_s3(filename)
            
        elif function_name == 'delete_recipe_from_s3':
            filename = parameters.get('filename')
            if not filename:
                raise ValueError("filename parameter is required")
            result = delete_recipe_from_s3(filename)
            
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
