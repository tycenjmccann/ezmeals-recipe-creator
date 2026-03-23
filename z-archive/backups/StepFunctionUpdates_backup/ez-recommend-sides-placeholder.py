import json
import logging

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Placeholder Lambda function for recommending side dishes.
    
    This function will:
    1. Check if the recipe is a main dish (dishType == "main")
    2. If main dish: Query menu database for available sides and use LLM to recommend pairings
    3. If side dish: Pass through unchanged
    4. Update the recommendedSides field in the JSON
    
    For now, this is a placeholder that prints "Hello World" and passes data through.
    """
    
    try:
        # Log the incoming event for debugging
        logger.info(f"Received event: {json.dumps(event)}")
        
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
        logger.info(f"Processing dish type: {dish_type}")
        
        if dish_type == 'main':
            logger.info("Hello World - Processing main dish for side recommendations!")
            # TODO: Query menu database for available sides
            # TODO: Use LLM to recommend side pairings
            # TODO: Update recommendedSides field
            
            # For now, just log that we would process this
            logger.info("Would recommend sides for this main dish")
        else:
            logger.info("Hello World - Side dish detected, passing through unchanged")
        
        # Return the data unchanged for now (placeholder behavior)
        return {
            'statusCode': 200,
            'body': json.dumps(recipe_data) if isinstance(recipe_data, dict) else recipe_data
        }
        
    except Exception as e:
        logger.error(f"Error in recommend-sides function: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal server error',
                'details': str(e)
            })
        }
