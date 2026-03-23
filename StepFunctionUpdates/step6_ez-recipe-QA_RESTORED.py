import json
import boto3
import logging
from botocore.config import Config
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configure Bedrock client with optimized retry settings
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
            logger.warning(f"Access denied for model profile {pid}: {str(e)}")
            last_error = e
            continue
        except client.exceptions.ThrottlingException as e:
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

def extract_previous_processing_notes(event):
    """Extract processing notes from previous steps"""
    try:
        step_output = event.get('stepOutput', {})
        return step_output.get('processingNotes', [])
    except Exception as e:
        logger.error(f"Error extracting processing notes: {e}")
        return []

def add_processing_note(processing_notes, step, note):
    """Add a processing note for this step"""
    processing_notes.append(f"{step}: {note}")
    return processing_notes

def create_processing_summary(processing_notes):
    """Create a summary of processing steps for Claude"""
    if not processing_notes:
        return "Processing Summary: No processing notes available."
    
    summary = "Processing Summary:\n"
    for note in processing_notes:
        summary += f"• {note}\n"
    
    return summary.strip()

def create_qa_prompt(original_recipe, final_json, processing_summary):
    """Create the EXACT ORIGINAL QA prompt - DO NOT CHANGE THIS"""
    prompt = f"""You are provided 3 things, 1/ the original recipe text, the final processed recipe in json form, and a summary of the process steps for context. 

Original Recipe:
{original_recipe}

Final Processed Recipe:
{json.dumps(final_json, indent=2)}

{processing_summary}

Provide a CONCISE quality assessment that answers these questions:

**OVERALL QUALITY**: High - Publish! or !Review Needed - <state the reason for review here>

**SIDE DISHES** (if dishType=main):
• Do the recommended side dishes compliment the main dish well
• Using your best judgement, are there other side dishes that would work better

**Product Recomendations**:
• Would the recommended products be used and helpful for this recipe?
• Using your best judgement, are there other products that we should be recommending for this dish

**Culinary Improvements** (if applicable):
• As a culinary expert and chef, do you have any suggestions to improve this recipe? 
• Is the recipe Gluten Free w/ notes for substitutions? Same for vegitarian, slowcooker, instapot attributes. 

Keep it brief and practical. Assume the processing worked correctly, lean towards publishing unless red flags.
"""
    
    return prompt

def lambda_handler(event, context):
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting focused QA processing")
    
    # Extract previous processing notes
    processing_notes = extract_previous_processing_notes(event)
    logger.info(f"[{request_id}] Received {len(processing_notes)} processing notes for review")
    
    # Log the incoming event
    logger.info(f"[{request_id}] Received event: {json.dumps(event)}")
    
    # Extract the body from the event, which contains the previous step's output
    try:
        recipe_text = event.get('recipe', None)
        step_output = event.get('stepOutput', {}).get('body', None)

        # Log missing values for debugging
        if not recipe_text:
            logger.error(f"[{request_id}] Missing 'recipe' text in the input.")
        if not step_output:
            logger.error(f"[{request_id}] Missing 'stepOutput body' in the input.")

        if not recipe_text or not step_output:
            processing_notes = add_processing_note(processing_notes, "Step 6", "Missing required input data for QA review")
            raise ValueError("Either recipe text or stepOutput body is missing from input.")

        step_output_data = json.loads(step_output)  # Parse the nested JSON string
        ingredients = step_output_data.get('ingredients', {}).get('L', [])
        
        logger.info(f"[{request_id}] Successfully parsed recipe data with {len(ingredients)} ingredients")

    except json.JSONDecodeError as e:
        logger.error(f"[{request_id}] JSON parsing error: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 6", f"JSON parsing error: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'Invalid JSON in stepOutput: {str(e)}', 'processingNotes': processing_notes})
        }
    except Exception as e:
        logger.error(f"[{request_id}] Error processing input: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 6", f"Input processing error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Error processing input: {str(e)}', 'processingNotes': processing_notes})
        }

    # Create processing summary for Claude
    processing_summary = create_processing_summary(processing_notes)
    
    # Create the QA prompt with EXACT ORIGINAL FORMAT
    prompt = create_qa_prompt(recipe_text, step_output_data, processing_summary)
    
    # Prepare conversation for Claude
    conversation = [
        {
            "role": "user",
            "content": [{"text": prompt}]
        }
    ]

    # Interact with Claude using model fallback
    try:
        response = invoke_claude(
            conversation,
            {"maxTokens": 2048, "temperature": 0.7}  # Same settings as original
        )

        # Extract the response from Claude
        response_text = response["output"]["message"]["content"][0]["text"]

        # Log the response for debugging
        logger.info(f"[{request_id}] Focused QA response from Claude: {response_text}")

        # Add final processing note
        processing_notes = add_processing_note(processing_notes, "Step 6", "Focused QA review completed - quality assessment provided")

        # Return the final result with EXACT ORIGINAL FORMAT
        final_result = {
            'summary': response_text.strip(),
            'recipe': step_output_data,  # Clean recipe JSON
            'processingNotes': processing_notes  # Complete audit trail
        }

        logger.info(f"[{request_id}] Focused QA processing completed successfully")
        return {
            'statusCode': 200,
            'body': json.dumps(final_result)  # JSON STRING as expected
        }

    except (ClientError, Exception) as e:
        logger.error(f"[{request_id}] ERROR: Can't invoke Claude. Reason: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 6", f"QA processing failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Error processing with Claude', 
                'details': str(e),
                'recipe': step_output_data if 'step_output_data' in locals() else {},
                'processingNotes': processing_notes
            })
        }
