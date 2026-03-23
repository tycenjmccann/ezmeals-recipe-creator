import streamlit as st
import boto3
import json
import time

# Set the page layout to wide
st.set_page_config(layout="wide")

# Set the title of the app
st.title("Update Recipe files w/ Ingredient Objects & Affiliate Ideas")

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
    # Replace text input with file uploader
    uploaded_file = st.file_uploader("Upload your recipe JSON file:", type=['json'])

    # Add a submit button to start Step Functions workflow
    if st.button("Submit"):
        if uploaded_file is None:
            st.error("Please upload a recipe JSON file.")
        else:
            try:
                # Read the uploaded file content
                recipe_content = uploaded_file.read().decode('utf-8')

                # Start Step Functions workflow
                response = client.start_execution(
                    stateMachineArn='arn:aws:states:us-west-2:023392223961:stateMachine:JSON_UpdateForIngredients_Flow',
                    input=json.dumps({'recipe': recipe_content})
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

                        # Extract summary and existing_json sections
                        summary = body.get('summary', "No summary available")
                        existing_json = body.get('existing_json', {})

                        # Display the summary and existing_json sections separately
                        st.subheader("Processing Summary:")
                        st.write(summary)  # Display the summary text

                        st.subheader("Existing JSON:")
                        st.json(existing_json)  # Display JSON structure of existing_json
                    else:
                        st.error("The 'body' field is missing in stepOutput. Check the workflow configuration.")

                elif execution_response['status'] in ['FAILED', 'TIMED_OUT', 'ABORTED']:
                    st.error(f"Workflow ended with status: {execution_response['status']}")
                else:
                    st.info("Workflow is still running. Please wait and click 'Check Status' again.")

            except Exception as e:
                st.error(f"An error occurred while fetching the workflow status: {e}")
    else:
        st.write("Submit a recipe to start processing.")
