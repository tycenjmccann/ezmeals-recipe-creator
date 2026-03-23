import json
import boto3
import logging
import re
import time
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

# Configuration
CONFIG = {
    'MODEL_ID': "us.anthropic.claude-opus-4-6-v1",
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

# ENHANCED Claude prompt template - with complete product information and food exclusion
CLAUDE_PROMPT_TEMPLATE = """
You are an ecommerce expert analyzing a recipe to identify relevant affiliate products that would help someone cook this dish.

Recipe Context:
Title: {title}
Cuisine Type: {cuisine_type}
Description: {description}

Recipe Ingredients:
{ingredients}

Recipe Instructions:
{instructions}

ALL Available Affiliate Products (complete information):
{affiliate_products}

Your task:
- Analyze the recipe for products that would be directly useful for cooking this dish
- Focus on TOOLS, EQUIPMENT, and NON-FOOD items that enhance the cooking process
- Consider specialized equipment mentioned or implied in the recipe
- Look for quality tools that would improve technique or results
- Consider storage, serving, or presentation items related to this dish type

IMPORTANT RESTRICTIONS:
- DO NOT recommend any food items, ingredients, or consumables (like frozen french fries, spices, oils, etc.)

Evaluation criteria:
- Equipment explicitly mentioned or implied in the recipe
- Tools that would improve cooking technique or results
- Specialized items for this cuisine type or cooking method
- Quality upgrades for standard kitchen equipment
- Storage or serving items specific to this dish type
- ONLY INCLUDE if this item be used to prepare the recipe provided. 

Return ONLY a JSON array of product IDs for NON-FOOD items that are relevant:
[
    "product-id-1",
    "product-id-2",
    "product-id-3"
]

Rules:
- Return only product IDs from the provided list
- Maximum 6 products
- Return empty array [] if no relevant non-food products found
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
            RoleSessionName="AffiliateProductsAccessSession"
        )
        return response['Credentials']
    except ClientError as e:
        logger.error(f"Error assuming role: {e}")
        raise

def extract_recipe_context(step_output_data):
    """Extract minimal context for affiliate product matching."""
    return {
        'title': step_output_data.get('title', {}).get('S', 'Unknown Recipe'),
        'cuisine_type': step_output_data.get('cuisineType', {}).get('S', 'Unknown'),
        'description': step_output_data.get('description', {}).get('S', '')
    }

def extract_recipe_content(step_output_data):
    """Extract ingredients and instructions for affiliate product analysis."""
    # Extract ingredients
    ingredients = step_output_data.get('ingredients', {}).get('L', [])
    ingredients_list = [item.get('S', '') for item in ingredients if item.get('S', '').strip()]
    
    # Extract instructions
    instructions = step_output_data.get('instructions', {}).get('L', [])
    instructions_list = [item.get('S', '') for item in instructions if item.get('S', '').strip()]
    
    return ingredients_list, instructions_list

def get_all_affiliate_products(credentials):
    """Get ALL affiliate products - let Claude do the filtering with complete information."""
    all_products = get_affiliate_products_cached(
        credentials['AccessKeyId'],
        credentials['SecretAccessKey'],
        credentials['SessionToken']
    )
    
    if not all_products:
        return []
    
    logger.info(f"Retrieved all {len(all_products)} affiliate products for Claude evaluation")
    return all_products

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

def create_claude_prompt(recipe_context, ingredients_list, instructions_list, affiliate_products):
    """Generate prompt for Claude with complete affiliate product information."""
    # Send complete affiliate product information to Claude
    products_for_prompt = [{
        'id': product.get('id'),
        'name': product.get('productName'),
        'description': product.get('description'),
        'inAppText': product.get('inAppText'),
        'category': product.get('category'),
        'linkLocation': product.get('linkLocation', []),
        'usedInMenuItem': product.get('usedInMenuItem', []),
        'price': product.get('price')
    } for product in affiliate_products]
    
    return CLAUDE_PROMPT_TEMPLATE.format(
        title=recipe_context['title'],
        cuisine_type=recipe_context['cuisine_type'],
        description=recipe_context['description'],
        ingredients=safe_json_dumps(ingredients_list, indent=2),
        instructions=safe_json_dumps(instructions_list[:5], indent=2),  # First 5 instructions for context
        affiliate_products=safe_json_dumps(products_for_prompt, indent=2)
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

def extract_product_ids(response_text):
    """Extract product IDs array from Claude's response."""
    logger.info(f"Raw Claude response length: {len(response_text)} characters")
    
    # Look for JSON array structure
    json_match = re.search(r'\[.*?\]', response_text, re.DOTALL)
    if not json_match:
        logger.error(f"No JSON array found in Claude response: {response_text}")
        raise ValueError("No product IDs array found in Claude's response")
    
    json_str = json_match.group(0)
    logger.info(f"Extracted JSON array: {json_str}")
    
    try:
        product_ids = json.loads(json_str)
        if not isinstance(product_ids, list):
            raise ValueError("Claude response must be a list of product IDs")
        
        logger.info(f"Successfully parsed {len(product_ids)} product IDs")
        return product_ids
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed. Extracted string: {json_str}")
        raise ValueError(f"Invalid JSON in Claude's response: {e}")

def validate_product_ids(product_ids, available_products):
    """Validate that product IDs exist in our database."""
    if not isinstance(product_ids, list):
        raise ValueError("Product IDs must be a list")
    
    valid_product_ids = {p.get('id') for p in available_products}
    valid_ids = [pid for pid in product_ids if pid in valid_product_ids]
    
    logger.info(f"Validation: {len(valid_ids)} valid out of {len(product_ids)} suggested product IDs")
    return valid_ids

def get_product_names(product_ids, available_products):
    """Get the names of products from their IDs."""
    id_to_name = {p.get('id'): p.get('productName', 'Unknown') for p in available_products}
    product_names = [id_to_name.get(pid, 'Unknown') for pid in product_ids]
    return product_names

def merge_product_ids(original_json, product_ids, request_id):
    """Safely merge product IDs back into the original JSON."""
    
    logger.info(f"[{request_id}] Starting products merge validation")
    
    # Validate original JSON structure
    if not isinstance(original_json, dict):
        raise ValueError(f"Original JSON must be a dictionary, got: {type(original_json)}")
    
    if 'products' not in original_json:
        raise ValueError("Original JSON missing 'products' field for merge")
    
    if not isinstance(original_json['products'], dict) or 'L' not in original_json['products']:
        raise ValueError("Original products field has wrong structure")
    
    logger.info(f"[{request_id}] Original JSON structure validation passed")
    
    # Convert product IDs to DynamoDB format
    products_dynamodb = {'L': [{'S': pid} for pid in product_ids]}
    
    # Store original for comparison
    original_products = original_json['products']['L']
    logger.info(f"[{request_id}] Replacing {len(original_products)} products with {len(product_ids)} new products")
    
    # Perform the merge
    original_json['products'] = products_dynamodb
    
    # Validate the merge worked correctly
    if len(original_json['products']['L']) != len(product_ids):
        raise ValueError("Merge validation failed - product count mismatch")
    
    logger.info(f"[{request_id}] Successfully merged {len(product_ids)} products")
    logger.info(f"[{request_id}] Products merge validation: All checks passed")
    
    return original_json

def lambda_handler(event, context):
    """Main Lambda handler with optimized context window usage and processing notes with product names."""
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting affiliate products processing")
    
    # Extract previous processing notes
    processing_notes = extract_previous_processing_notes(event)
    logger.info(f"[{request_id}] Received {len(processing_notes)} previous processing notes")
    
    try:
        # Validate input
        recipe_text, step_output = validate_input(event)
        logger.info(f"[{request_id}] Input validation successful")
        
        # Parse step output
        try:
            step_output_data = json.loads(step_output)
            logger.info(f"[{request_id}] Successfully parsed step output data")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"[{request_id}] JSON parsing failed: {e}")
            processing_notes = add_processing_note(processing_notes, "Step 5", f"JSON parsing error: {str(e)}")
            return {
                'statusCode': 400,
                'body': safe_json_dumps({'error': 'Invalid input or JSON parsing error.'}),
                'processingNotes': processing_notes
            }
        
        # Extract recipe context and content
        logger.info(f"[{request_id}] Extracting recipe context and content")
        recipe_context = extract_recipe_context(step_output_data)
        ingredients_list, instructions_list = extract_recipe_content(step_output_data)
        logger.info(f"[{request_id}] Processing recipe: {recipe_context['title']} with {len(ingredients_list)} ingredients")
        
        # Assume cross-account role
        logger.info(f"[{request_id}] Assuming cross-account role")
        credentials = assume_cross_account_role()
        
        # Get all affiliate products (no filtering)
        logger.info(f"[{request_id}] Retrieving all affiliate products")
        affiliate_products = get_all_affiliate_products(credentials)
        
        if not affiliate_products:
            logger.warning(f"[{request_id}] No affiliate products found in database")
            processing_notes = add_processing_note(processing_notes, "Step 5", "No affiliate products found in database")
            return {
                'statusCode': 200,
                'body': safe_json_dumps(step_output_data),
                'processingNotes': processing_notes
            }
        
        logger.info(f"[{request_id}] Found {len(affiliate_products)} total affiliate products for evaluation")
        
        # Create enhanced prompt with complete product information
        logger.info(f"[{request_id}] Creating enhanced Claude prompt with complete affiliate product information")
        prompt = create_claude_prompt(recipe_context, ingredients_list, instructions_list, affiliate_products)
        logger.info(f"[{request_id}] Prompt size: {len(prompt)} characters")
        
        # Get product IDs from Claude
        logger.info(f"[{request_id}] Invoking Claude for product recommendations")
        conversation = [{"role": "user", "content": [{"text": prompt}]}]
        claude_response = call_bedrock_api(conversation)
        response_text = claude_response["output"]["message"]["content"][0]["text"]
        
        # Extract and validate product IDs
        logger.info(f"[{request_id}] Extracting product IDs from response")
        product_ids = extract_product_ids(response_text)
        
        logger.info(f"[{request_id}] Validating product IDs")
        valid_product_ids = validate_product_ids(product_ids, affiliate_products)
        
        # Get product names for processing notes
        product_names = get_product_names(valid_product_ids, affiliate_products)
        
        # Add processing notes based on results with product names
        if valid_product_ids:
            processing_notes = add_processing_note(processing_notes, "Step 5", f"Added {len(valid_product_ids)} affiliate products: {', '.join(product_names)}")
        else:
            processing_notes = add_processing_note(processing_notes, "Step 5", "No valid affiliate products found for this recipe")
        
        # Merge back into original JSON
        logger.info(f"[{request_id}] Merging product IDs into original JSON")
        updated_json = merge_product_ids(step_output_data, valid_product_ids, request_id)
        
        logger.info(f"[{request_id}] Successfully processed affiliate products")
        return {
            'statusCode': 200,
            'body': safe_json_dumps(updated_json),
            'processingNotes': processing_notes
        }
        
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 5", f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': safe_json_dumps({
                'error': 'Unexpected error',
                'details': str(e)
            }),
            'processingNotes': processing_notes
        }
