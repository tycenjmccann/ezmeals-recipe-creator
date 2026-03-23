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

def lambda_handler(event, context):
    """
    Side dish recommendation function with processing notes support.
    
    This function will:
    1. Check if the recipe is a main dish (dishType == "main")
    2. If main dish: Query menu database for available sides and use LLM to recommend pairings
    3. If side dish: Pass through unchanged
    4. Update the recommendedSides field in the JSON
    5. Track all processing in processingNotes
    """
    
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting side dish recommendation processing")
    
    # Extract previous processing notes
    processing_notes = extract_previous_processing_notes(event)
    logger.info(f"[{request_id}] Received {len(processing_notes)} previous processing notes")
    
    try:
        # Log the incoming event for debugging
        logger.info(f"[{request_id}] Received event: {json.dumps(event)}")
        
        # Extract the recipe data from stepOutput
        step_output = event.get('stepOutput', {})
        recipe_data = None
        
        # Handle different stepOutput formats (string or dict)
        if isinstance(step_output, dict):
            body = step_output.get('body')
            if isinstance(body, str):
                recipe_data = json.loads(body)
            elif isinstance(body, dict):
                recipe_data = body
        elif isinstance(step_output, str):
            recipe_data = json.loads(step_output)
            
        if not recipe_data:
            raise ValueError("Could not extract recipe data from stepOutput")
        
        # Check if this is a main dish
        dish_type = recipe_data.get('dishType', {}).get('S', '')
        logger.info(f"[{request_id}] Processing dish type: {dish_type}")
        
        if dish_type == 'main':
            logger.info(f"[{request_id}] Main dish detected - side recommendations needed")
            processing_notes = add_processing_note(processing_notes, "Step 4", "Main dish detected - processing side recommendations")
            
            # TODO: Query menu database for available sides
            # TODO: Use LLM to recommend side pairings
            # TODO: Update recommendedSides field
            
            # For now, just log that we would process this
            logger.info(f"[{request_id}] Would recommend sides for this main dish")
            processing_notes = add_processing_note(processing_notes, "Step 4", "Side recommendation logic not yet implemented - placeholder processing")
            
        else:
            logger.info(f"[{request_id}] Side dish detected, passing through unchanged")
            processing_notes = add_processing_note(processing_notes, "Step 4", "Side dish detected - skipped side recommendations")
        
        # Return the data with processing notes (placeholder behavior for now)
        return {
            'statusCode': 200,
            'body': json.dumps(recipe_data),
            'processingNotes': processing_notes
        }
        
    except Exception as e:
        logger.error(f"[{request_id}] Error in recommend-sides function: {str(e)}")
        # Add error to processing notes and continue
        processing_notes = add_processing_note(processing_notes, "Step 4", f"Error in side recommendation: {str(e)}")
        
        # Try to return original data with error note
        try:
            step_output = event.get('stepOutput', {})
            body = step_output.get('body')
            if isinstance(body, str):
                recipe_data = json.loads(body)
            elif isinstance(body, dict):
                recipe_data = body
            else:
                recipe_data = {}
                
            return {
                'statusCode': 200,
                'body': json.dumps(recipe_data),
                'processingNotes': processing_notes
            }
        except:
            # If we can't even extract the original data, bubble up the error
            raise
