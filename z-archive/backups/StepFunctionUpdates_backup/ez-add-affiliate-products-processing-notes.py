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
You are provided with text containing the original recipe and the same recipe that's been modified and converted to json format.

Your role as an ecommerce expert is to review both the original recipe information and the json formatted recipe to identify potential products that could be sold through Amazon's affiliate marketing program.

Here are the available affiliate products to consider:
{affiliate_products}

If you identify relevant products:
- Update the existing_json "products" list by adding the product ids
- Only include products that are directly relevant to the recipe
- Consider both explicitly mentioned items and implied needs (e.g., specialized equipment)
- Use the product's inAppText and usedInMenuItem fields to help determine relevance

Critical requirements:
- Keep the products attribute as a list of strings (product IDs)
- Do not modify any other JSON attributes
- Return only the updated JSON, no explanations
- Only suggest products from the provided affiliate product list

Original recipe:
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
            RoleSessionName="AffiliateProductsAccessSession"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise

@lru_cache(maxsize=1)
def get_affiliate_products_cached(access_key, secret_key, session_token):
    """Cached version of affiliate products fetch with pagination"""
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
            # Filter only items where linkLocation contains 'products'
            filtered_items = [item for item in response.get('Items', []) 
                            if item.get('linkLocation') and 
                            'products' in [loc.lower() for loc in item.get('linkLocation', [])]]
            items.extend(filtered_items)
            
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break
                
        logger.info(f"Retrieved {len(items)} affiliate products")
        return items
    
    except ClientError as e:
        logger.error(f"Error retrieving products from DynamoDB: {e}")
        raise

def get_affiliate_products(credentials):
    """Wrapper function to call the cached version with unpacked credentials"""
    return get_affiliate_products_cached(
        credentials['AccessKeyId'],
        credentials['SecretAccessKey'],
        credentials['SessionToken']
    )

def get_claude_prompt(recipe_text, existing_json, affiliate_products):
    """Generate formatted prompt for Claude"""
    products_for_prompt = [{
        'id': product.get('id'),
        'name': product.get('productName'),
        'description': product.get('description'),
        'inAppText': product.get('inAppText'),
        'usedInMenuItem': product.get('usedInMenuItem')
    } for product in affiliate_products]
    
    return CLAUDE_PROMPT_TEMPLATE.format(
        recipe_text=recipe_text,
        existing_json=existing_json,
        affiliate_products=json.dumps(products_for_prompt, indent=2)
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

def log_metrics(start_time, response):
    """Log execution metrics"""
    execution_time = time.time() - start_time
    logger.info({
        'metric_name': 'ProcessingTime',
        'value': execution_time,
        'unit': 'Seconds',
        'status': response['statusCode']
    })

def lambda_handler(event, context):
    start_time = time.time()
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting affiliate products processing")
    
    # Extract previous processing notes
    processing_notes = extract_previous_processing_notes(event)
    logger.info(f"[{request_id}] Received {len(processing_notes)} previous processing notes")
    
    try:
        # Validate input
        recipe_text, step_output = validate_input(event)
        logger.info(f"[{request_id}] Input validation successful")
        
        # Assume cross-account role
        credentials = assume_cross_account_role()
        if not credentials:
            processing_notes = add_processing_note(processing_notes, "Step 5", "Could not assume cross-account role")
            response = {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not assume cross-account role'}),
                'processingNotes': processing_notes
            }
            log_metrics(start_time, response)
            return response

        # Get affiliate products
        affiliate_products = get_affiliate_products(credentials)
        if not affiliate_products:
            processing_notes = add_processing_note(processing_notes, "Step 5", "No affiliate products found in database")
            response = {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not retrieve affiliate products'}),
                'processingNotes': processing_notes
            }
            log_metrics(start_time, response)
            return response

        logger.info(f"[{request_id}] Retrieved {len(affiliate_products)} affiliate products")

        # Parse step output
        try:
            step_output_data = json.loads(step_output)
            logger.info(f"[{request_id}] Successfully parsed step output data")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"[{request_id}] Error processing input: {e}")
            processing_notes = add_processing_note(processing_notes, "Step 5", f"JSON parsing error: {str(e)}")
            response = {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid input or JSON parsing error.'}),
                'processingNotes': processing_notes
            }
            log_metrics(start_time, response)
            return response

        # Prepare and send prompt to Claude
        prompt = get_claude_prompt(recipe_text, json.dumps(step_output_data, indent=2), affiliate_products)
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
                
                # Count and validate products
                products_added = 0
                if 'products' in updated_body:
                    valid_product_ids = {p.get('id') for p in affiliate_products}
                    
                    # Extract product IDs from DynamoDB format
                    current_products = [item['S'] for item in updated_body['products']['L']]
                    logger.info(f"[{request_id}] Current product IDs: {current_products}")
                    
                    # Filter valid products
                    valid_products = [
                        pid for pid in current_products 
                        if pid in valid_product_ids
                    ]
                    logger.info(f"[{request_id}] Valid product IDs: {valid_products}")
                    
                    # Update in DynamoDB format
                    updated_body['products'] = {
                        'L': [{'S': pid} for pid in valid_products]
                    }
                    
                    products_added = len(valid_products)
                
                # Add processing notes based on results
                if products_added > 0:
                    processing_notes = add_processing_note(processing_notes, "Step 5", f"Added {products_added} affiliate products")
                else:
                    processing_notes = add_processing_note(processing_notes, "Step 5", "No relevant affiliate products found for this recipe")
                
                final_response = {
                    'statusCode': 200,
                    'body': json.dumps(updated_body),
                    'processingNotes': processing_notes
                }
                logger.info(f"[{request_id}] Final response being returned:")
                logger.info(json.dumps(final_response, indent=2))
                
                log_metrics(start_time, final_response)
                return final_response
                
            except (json.JSONDecodeError, ValueError) as je:
                logger.error(f"[{request_id}] JSON parsing error: {je}")
                logger.error(f"[{request_id}] Problematic JSON string:")
                logger.error(json_str if 'json_str' in locals() else "No JSON string extracted")
                processing_notes = add_processing_note(processing_notes, "Step 5", f"Claude response parsing error: {str(je)}")
                raise ValueError("Unable to parse Claude's response as valid JSON")
            
        except (ClientError, ValueError, Exception) as e:
            logger.error(f"[{request_id}] Error processing with Claude: {e}")
            if 'response_text' in locals():
                logger.error(f"[{request_id}] Claude's raw response:")
                logger.error(response_text)
            processing_notes = add_processing_note(processing_notes, "Step 5", f"Claude processing error: {str(e)}")
            response = {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Error processing with Claude',
                    'details': str(e)
                }),
                'processingNotes': processing_notes
            }
            log_metrics(start_time, response)
            return response
            
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 5", f"Unexpected error: {str(e)}")
        response = {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Unexpected error',
                'details': str(e)
            }),
            'processingNotes': processing_notes
        }
        log_metrics(start_time, response)
        return response
