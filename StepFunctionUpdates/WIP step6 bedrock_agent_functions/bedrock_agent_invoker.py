import json
import boto3
import logging
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Bedrock Agent Runtime client
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name='us-west-2')

def lambda_handler(event, context):
    """
    Lambda function to invoke Bedrock agent for recipe QA.
    Maintains the same response format as the original ez-recipe-QA function.
    """
    try:
        logger.info(f"Received event: {json.dumps(event, default=str)}")
        
        # Prepare input for the agent
        input_text = f"Please review this recipe processing output and make any necessary improvements. Return response in the exact JSON format expected: {json.dumps(event, default=str)}"
        
        # Invoke the Bedrock agent
        response = bedrock_agent_runtime.invoke_agent(
            agentId='527PUILYQ5',
            agentAliasId='6GGZX37ZW1',
            sessionId=context.aws_request_id,
            inputText=input_text
        )
        
        # Process the agent response
        agent_response = ""
        if 'completion' in response:
            for event_chunk in response['completion']:
                if 'chunk' in event_chunk:
                    chunk = event_chunk['chunk']
                    if 'bytes' in chunk:
                        agent_response += chunk['bytes'].decode('utf-8')
        
        logger.info(f"Agent response: {agent_response}")
        
        # Try to parse the agent response as JSON
        try:
            parsed_response = json.loads(agent_response)
            if 'statusCode' in parsed_response and 'body' in parsed_response:
                return parsed_response
        except json.JSONDecodeError:
            logger.warning("Agent response is not valid JSON, wrapping in standard format")
        
        # If agent response is not in expected format, wrap it
        return {
            "statusCode": 200,
            "body": {
                "summary": f"🤖 **AGENT RESPONSE**:\n\n{agent_response}",
                "recipe": event.get('stepOutput', {}).get('body', {}),
                "processingNotes": event.get('stepOutput', {}).get('processingNotes', []) + [
                    "Step 6: Agent review completed"
                ]
            }
        }
        
    except ClientError as e:
        logger.error(f"Error invoking Bedrock agent: {e}")
        # Fallback response in case of error
        return {
            "statusCode": 500,
            "body": {
                "summary": f"⚠️ **AGENT ERROR**: Failed to process recipe - {str(e)}",
                "recipe": event.get('stepOutput', {}).get('body', {}),
                "processingNotes": event.get('stepOutput', {}).get('processingNotes', []) + [
                    f"Step 6: Agent error - {str(e)}"
                ]
            }
        }
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        # Fallback response for any other error
        return {
            "statusCode": 500,
            "body": {
                "summary": f"⚠️ **SYSTEM ERROR**: Unexpected error - {str(e)}",
                "recipe": event.get('stepOutput', {}).get('body', {}),
                "processingNotes": event.get('stepOutput', {}).get('processingNotes', []) + [
                    f"Step 6: System error - {str(e)}"
                ]
            }
        }
