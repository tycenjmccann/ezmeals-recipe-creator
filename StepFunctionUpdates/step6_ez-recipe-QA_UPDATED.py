import json
import boto3
import logging
from botocore.config import Config
from botocore.exceptions import ClientError
from typing import Dict, List, Any

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configure Bedrock client
config = Config(
    region_name="us-west-2",
    connect_timeout=120,
    read_timeout=180,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
)

client = boto3.client("bedrock-runtime", config=config)

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
            response = client.converse(
                modelId=pid,
                messages=messages,
                inferenceConfig=cfg
            )
            logger.info(f"Successfully used model profile: {pid}")
            return response
        except client.exceptions.AccessDeniedException as e:
            # profile exists but model not enabled in this account/Region
            logger.warning(f"Access denied for model profile {pid}: {str(e)}")
            last_error = e
            continue
        except client.exceptions.ThrottlingException as e:
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

def call_claude_for_qa_review(recipe_data: Dict, processing_notes: List[str]) -> str:
    """
    Call Claude for focused QA review with culinary improvements.
    """
    
    # Extract key recipe information for review
    recipe_title = recipe_data.get('title', {}).get('S', 'Unknown Recipe')
    cuisine_type = recipe_data.get('cuisineType', {}).get('S', 'Unknown')
    prep_time = recipe_data.get('prepTime', {}).get('N', '0')
    cook_time = recipe_data.get('cookTime', {}).get('N', '0')
    
    # Extract ingredients and instructions
    ingredients = [item.get('S', '') for item in recipe_data.get('ingredients', {}).get('L', [])]
    instructions = [item.get('S', '') for item in recipe_data.get('instructions', {}).get('L', [])]
    
    # Create focused prompt for QA review
    prompt = f"""
Provide a focused quality assessment for this recipe with practical culinary improvements.

Recipe: {recipe_title}
Cuisine: {cuisine_type}
Prep Time: {prep_time} min | Cook Time: {cook_time} min

Ingredients ({len(ingredients)} items):
{chr(10).join([f"• {ing}" for ing in ingredients[:10]])}
{"..." if len(ingredients) > 10 else ""}

Instructions ({len(instructions)} steps):
{chr(10).join([f"{i+1}. {inst}" for i, inst in enumerate(instructions[:8])])}
{"..." if len(instructions) > 8 else ""}

Processing Notes:
{chr(10).join([f"• {note}" for note in processing_notes])}

Provide a concise quality assessment in this format:

**OVERALL QUALITY**: [Good/Excellent/Needs Improvement] - [Brief assessment]

**KEY DIFFERENCES** (if any significant processing changes were made):
• [List any important changes from processing notes]

**CULINARY IMPROVEMENTS**:
• [Practical cooking tip 1]
• [Practical cooking tip 2]
• [Practical cooking tip 3]

Focus on:
- Recipe quality and accuracy
- Practical cooking advice
- Ingredient or technique improvements
- Time/temperature optimizations

Keep response under 300 words and focus on actionable culinary advice.
"""
    
    conversation = [{"role": "user", "content": [{"text": prompt}]}]
    
    response = invoke_claude(
        conversation,
        {"maxTokens": 2048, "temperature": 0.3}  # Lower temperature for consistent, focused output
    )
    
    return response['output']['message']['content'][0]['text']

def lambda_handler(event, context):
    """
    AWS Lambda handler for focused QA review with culinary improvements.
    """
    logger.info("Starting focused QA review")
    
    try:
        # Extract data from the event
        step_output = event.get('stepOutput', {})
        recipe_json_str = step_output.get('body', '{}')
        processing_notes = step_output.get('processingNotes', [])
        
        # Parse the recipe JSON
        recipe_data = json.loads(recipe_json_str)
        
        logger.info(f"Performing QA review for recipe with {len(processing_notes)} processing notes")
        
        # Call Claude for focused QA review
        qa_summary = call_claude_for_qa_review(recipe_data, processing_notes)
        
        # Create final response with clean separation
        final_response = {
            "summary": qa_summary,
            "recipe": recipe_data,
            "processingNotes": processing_notes + ["Step 6: Focused QA review completed - quality assessment provided"]
        }
        
        logger.info("QA review completed successfully")
        
        return {
            'statusCode': 200,
            'body': final_response
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        return {
            'statusCode': 400,
            'body': {'error': f'Invalid JSON: {str(e)}'},
            'processingNotes': processing_notes + [f"Step 6: JSON parsing error - {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': {'error': f'Internal server error: {str(e)}'},
            'processingNotes': processing_notes + [f"Step 6: Internal server error - {str(e)}"]
        }
