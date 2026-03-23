import streamlit as st
import boto3
import json
import time

# Set the page layout to wide
st.set_page_config(layout="wide")

# Set the title of the app
st.title("Recipe Creator")

# AWS Step Functions client
client = boto3.client('stepfunctions', region_name='us-west-2')

# Create two columns
col1, col2 = st.columns([10, 10])

# Variable to store execution ARN for later querying
if 'execution_arn' not in st.session_state:
    st.session_state['execution_arn'] = None

# Left column for input
with col1:
    st.header("Input Recipe")
    recipe_text = st.text_area("Enter your recipe here:")

    # Add a submit button to start Step Functions workflow
    if st.button("Submit"):
        if recipe_text.strip() == "":
            st.error("Please enter a recipe.")
        else:
            try:
                # Start Step Functions workflow
                response = client.start_execution(
                    stateMachineArn='arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2',
                    input=json.dumps({'recipe': recipe_text})
                )
                # Store the execution ARN in the session state
                st.session_state['execution_arn'] = response['executionArn']
                st.session_state['workflow_status'] = "RUNNING"
                st.success("Recipe submitted successfully! Execution ARN: " + response['executionArn'])
            except Exception as e:
                st.error(f"An error occurred: {e}")

# Right column for displaying results
with col2:
    st.header("Processing Results")

    # Check if an execution ARN is available
    if st.session_state['execution_arn']:
        # Get the status of the workflow execution
        if st.button("Check Status"):
            try:
                execution_response = client.describe_execution(
                    executionArn=st.session_state['execution_arn']
                )
                st.session_state['workflow_status'] = execution_response['status']

                if execution_response['status'] == 'SUCCEEDED':
                    # Parse the main output and extract stepOutput
                    output = json.loads(execution_response.get('output', '{}'))
                    step_output = output.get('stepOutput', {})

                    # Check if body is in step_output
                    if 'body' in step_output:
                        # Parse body as it is a JSON string within stepOutput
                        body = json.loads(step_output['body'])
                        
                        # Extract summary, recipe, and processingNotes from the new structure
                        summary = body.get('summary', "No summary available")
                        recipe_json = body.get('recipe', {})  # Changed from 'existing_json' to 'recipe'
                        processing_notes = body.get('processingNotes', [])

                        # Display the summary
                        st.subheader("QA Summary:")
                        st.write(summary)

                        # Display processing notes if available
                        if processing_notes:
                            st.subheader("Processing Notes:")
                            for i, note in enumerate(processing_notes, 1):
                                st.write(f"{i}. {note}")
                        
                        # Display the recipe JSON
                        st.subheader("Recipe JSON:")
                        if recipe_json:
                            st.json(recipe_json)
                        else:
                            st.warning("Recipe JSON is empty or not available")
                            
                        # Debug information (can be removed later)
                        with st.expander("Debug: Raw Response Structure"):
                            st.write("Keys in body:", list(body.keys()) if body else "No body")
                            st.json(body)
                            
                    else:
                        st.error("The 'body' field is missing in stepOutput. Check the workflow configuration.")
                        # Debug information
                        st.write("Available keys in stepOutput:", list(step_output.keys()) if step_output else "No stepOutput")
                        st.json(step_output)

                elif execution_response['status'] in ['FAILED', 'TIMED_OUT', 'ABORTED']:
                    st.error(f"Workflow ended with status: {execution_response['status']}")
                    # Show error details if available
                    if 'error' in execution_response:
                        st.error(f"Error details: {execution_response['error']}")
                    if 'cause' in execution_response:
                        st.error(f"Cause: {execution_response['cause']}")
                else:
                    st.info("Workflow is still running. Please wait and click 'Check Status' again.")

            except Exception as e:
                st.error(f"An error occurred while fetching the workflow status: {e}")
                # Additional debug information
                st.write("Exception details:", str(e))
    else:
        st.write("Submit a recipe to start processing.")
