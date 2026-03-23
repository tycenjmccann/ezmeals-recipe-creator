import json
import boto3
import logging
import time
from botocore.exceptions import ClientError, ReadTimeoutError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Bedrock Agent Runtime client with extended timeout
bedrock_agent_runtime = boto3.client(
    'bedrock-agent-runtime', 
    region_name='us-west-2',
    config=boto3.session.Config(
        read_timeout=300,  # 5 minutes
        connect_timeout=60,
        retries={'max_attempts': 3}
    )
)

def extract_previous_processing_notes(event):
    """Extract processing notes from previous steps."""
    step_output = event.get('stepOutput', {})
    if isinstance(step_output, dict) and 'processingNotes' in step_output:
        return step_output['processingNotes']
    return []

def add_processing_note(previous_notes, step_name, note):
    """Add a new processing note to the accumulated list."""
    new_notes = previous_notes.copy()
    new_notes.append(f"{step_name}: {note}")
    return new_notes

def invoke_agent_with_retry(agent_id, alias_id, session_id, input_text, max_retries=3):
    """Invoke Bedrock agent with retry logic and exponential backoff."""
    for attempt in range(max_retries):
        try:
            logger.info(f"Invoking agent, attempt {attempt + 1}/{max_retries}")
            response = bedrock_agent_runtime.invoke_agent(
                agentId=agent_id,
                agentAliasId=alias_id,
                sessionId=session_id,
                inputText=input_text
            )
            return response
        except ReadTimeoutError as e:
            logger.warning(f"Attempt {attempt + 1} timed out: {e}")
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + 1  # Exponential backoff
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                continue
            raise
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            raise

def lambda_handler(event, context):
    """
    Lambda function to invoke Bedrock agent for recipe QA.
    FIXED: Returns JSON string in body field for Streamlit compatibility.
    """
    request_id = context.aws_request_id
    logger.info(f"[{request_id}] Starting Bedrock agent QA processing")
    
    # Extract previous processing notes
    processing_notes = extract_previous_processing_notes(event)
    
    try:
        # Extract recipe data
        recipe_text = event.get('recipe', '')
        step_output = event.get('stepOutput', {})
        recipe_data = step_output.get('body', {})
        
        # Parse recipe data if it's a JSON string
        if isinstance(recipe_data, str):
            try:
                recipe_data = json.loads(recipe_data)
            except json.JSONDecodeError:
                logger.warning(f"[{request_id}] Could not parse recipe data")
        
        # Prepare input for agent
        input_text = f"""Review this recipe and provide quality assessment:

Original Recipe: {recipe_text}
Processed Data: {json.dumps(recipe_data, indent=2, default=str)}
Processing Notes: {chr(10).join(processing_notes)}

Provide quality assessment and improvement suggestions."""
        
        # Invoke agent with retry
        response = invoke_agent_with_retry(
            agent_id='527PUILYQ5',
            alias_id='TSTALIASID',
            session_id=request_id,
            input_text=input_text
        )
        
        # Process agent response
        agent_response = ""
        if 'completion' in response:
            for event_chunk in response['completion']:
                if 'chunk' in event_chunk:
                    chunk = event_chunk['chunk']
                    if 'bytes' in chunk:
                        agent_response += chunk['bytes'].decode('utf-8')
        
        # Add success note
        processing_notes = add_processing_note(
            processing_notes, "Step 6", "Bedrock agent QA completed"
        )
        
        # Create final result
        final_result = {
            "summary": f"🤖 **BEDROCK AGENT REVIEW**:\n\n{agent_response}",
            "recipe": recipe_data,
            "processingNotes": processing_notes
        }
        
        # CRITICAL FIX: Return JSON string in body
        return {
            "statusCode": 200,
            "body": json.dumps(final_result)  # ← JSON STRING (Fixed!)
        }
        
    except ReadTimeoutError as e:
        processing_notes = add_processing_note(
            processing_notes, "Step 6", f"Agent timeout - {str(e)}"
        )
        
        fallback_result = {
            "summary": "⚠️ **PARTIAL**: Recipe processed through Step 5. Agent timed out.",
            "recipe": recipe_data if 'recipe_data' in locals() else {},
            "processingNotes": processing_notes
        }
        
        return {
            "statusCode": 200,
            "body": json.dumps(fallback_result)  # ← JSON STRING (Fixed!)
        }
        
    except Exception as e:
        processing_notes = add_processing_note(
            processing_notes, "Step 6", f"Error - {str(e)}"
        )
        
        error_result = {
            "summary": f"⚠️ **ERROR**: {str(e)}",
            "recipe": recipe_data if 'recipe_data' in locals() else {},
            "processingNotes": processing_notes
        }
        
        return {
            "statusCode": 500,
            "body": json.dumps(error_result)  # ← JSON STRING (Fixed!)
        }
