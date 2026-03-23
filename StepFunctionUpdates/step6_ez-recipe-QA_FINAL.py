import json
import boto3
import logging
import re
from botocore.config import Config
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configure the Boto3 client with extended timeout settings and retry logic
config = Config(
    region_name="us-west-2",
    connect_timeout=120,
    read_timeout=180,
    retries={
        'max_attempts': 10,
        'mode': 'standard'
    }
)

# Create a Bedrock Runtime client with the updated config
client = boto3.client("bedrock-runtime", config=config)

# Model ID for Claude 3 Opus
model_id = "us.anthropic.claude-opus-4-6-v1"

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

def create_focused_qa_prompt(original_recipe, final_json, processing_notes):
    """Create focused QA prompt emphasizing quality comparison and culinary improvements."""
    
    processing_summary = ""
    if processing_notes:
        processing_summary = f"""
Processing Summary:
{chr(10).join(processing_notes)}

"""
    else:
        processing_summary = "Processing Summary: No processing changes were made.\n\n"
    
    prompt = f"""
You are a culinary expert and Product Manager for the ezMeals meal planning iOS app. You are to review the provided recipe, it's history, and determine if the final recipe is ready to publish to our users.
You are provided 3 things, 1/ the original recipe text, the final processed recipe in json form, and a summary of the process steps for context. 

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
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"[{request_id}] Error processing input: {e}")
        processing_notes = add_processing_note(processing_notes, "Step 6", f"Input processing error: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Invalid input or ingredients list missing.',
                'processingNotes': processing_notes
            })
        }

    # Prepare the focused input for Claude
    original_recipe = recipe_text
    
    # Create focused prompt emphasizing quality and culinary improvements
    prompt = create_focused_qa_prompt(original_recipe, step_output_data, processing_notes)

    # Log the prompt being sent to Claude
    logger.info(f"[{request_id}] Prompt being sent to Claude: {prompt}")

    # Set up the conversation for Claude
    conversation = [
        {
            "role": "user",
            "content": [{"text": prompt}],
        }
    ]

    # Interact with Claude 3 Opus using the Bedrock runtime
    try:
        response = client.converse(
            modelId=model_id,
            messages=conversation,
            inferenceConfig={"maxTokens": 2048, "temperature": 0.7},  # Reduced tokens for more concise response
            additionalModelRequestFields={"top_k": 250}
        )

        # Extract the response from Claude
        response_text = response["output"]["message"]["content"][0]["text"]

        # Log the response for debugging
        logger.info(f"[{request_id}] Focused QA response from Claude: {response_text}")

        # Add final processing note
        processing_notes = add_processing_note(processing_notes, "Step 6", "Focused QA review completed - quality assessment provided")

        # Return the final result with focused QA summary, clean recipe, and all processing notes
        final_result = {
            'summary': response_text.strip(),
            'recipe': step_output_data,  # Clean recipe JSON
            'processingNotes': processing_notes  # Complete audit trail
        }

        logger.info(f"[{request_id}] Focused QA processing completed successfully")
        return {
            'statusCode': 200,
            'body': json.dumps(final_result)
        }

    except (ClientError, Exception) as e:
        logger.error(f"[{request_id}] ERROR: Can't invoke '{model_id}'. Reason: {e}")
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
