import streamlit as st
import boto3
import json
import time
from datetime import datetime
from botocore.exceptions import ProfileNotFound, NoCredentialsError, ClientError

# Set the page layout to wide
st.set_page_config(layout="wide")

# Set the title of the app
st.title("Recipe Creator")

# Initialize sidebar for debug logging
st.sidebar.title("Debug Log")
if 'debug_log' not in st.session_state:
    st.session_state['debug_log'] = []

def add_debug_log(message):
    """Add message to debug log in sidebar"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state['debug_log'].append(f"[{timestamp}] {message}")
    # Keep only last 20 messages
    if len(st.session_state['debug_log']) > 20:
        st.session_state['debug_log'] = st.session_state['debug_log'][-20:]

def display_debug_log():
    """Display debug log in sidebar"""
    if st.session_state['debug_log']:
        for log_entry in reversed(st.session_state['debug_log']):  # Show newest first
            st.sidebar.text(log_entry)
    else:
        st.sidebar.text("No debug messages yet...")

# AWS Step Functions client (default/isengard account)
stepfunctions_client = boto3.client('stepfunctions', region_name='us-west-2')

# S3 bucket configuration
S3_BUCKET = 'menu-items-json'

# Initialize session state variables
if 'execution_arn' not in st.session_state:
    st.session_state['execution_arn'] = None
if 'recipe_data' not in st.session_state:
    st.session_state['recipe_data'] = None
if 'recipe_title' not in st.session_state:
    st.session_state['recipe_title'] = None
if 'workflow_status' not in st.session_state:
    st.session_state['workflow_status'] = None
if 'qa_summary' not in st.session_state:
    st.session_state['qa_summary'] = None
if 'processing_notes' not in st.session_state:
    st.session_state['processing_notes'] = []
if 'upload_success' not in st.session_state:
    st.session_state['upload_success'] = False

def get_s3_client():
    """Get S3 client using ezmeals profile with proper error handling"""
    try:
        add_debug_log("Attempting to create S3 client with ezmeals profile")
        
        # Create a session with the ezmeals profile first
        session = boto3.Session(profile_name='ezmeals')
        # Then create the S3 client from that session
        client = session.client('s3', region_name='us-west-1')
        
        add_debug_log("S3 client created successfully")
        return client
    except ProfileNotFound:
        add_debug_log("ERROR: AWS profile 'ezmeals' not found")
        st.error("❌ AWS profile 'ezmeals' not found. Please configure it with: `aws configure --profile ezmeals`")
        return None
    except NoCredentialsError:
        add_debug_log("ERROR: No AWS credentials found for 'ezmeals' profile")
        st.error("❌ No AWS credentials found for 'ezmeals' profile. Please check your credentials.")
        return None
    except Exception as e:
        add_debug_log(f"ERROR: Failed to create S3 client: {str(e)}")
        st.error(f"❌ Error creating S3 client: {str(e)}")
        return None

def upload_recipe_to_s3(recipe_json, recipe_title):
    """Upload recipe JSON to S3 bucket and save local copy using ezmeals profile"""
    try:
        add_debug_log("Starting upload_recipe_to_s3 function")
        
        # Get S3 client with error handling
        s3_client = get_s3_client()
        if not s3_client:
            add_debug_log("Failed to get S3 client")
            return None, False, "Failed to create S3 client - check ezmeals profile configuration", False, None, None
        
        # Create filename using just the recipe name (no timestamp for overwriting)
        clean_title = "".join(c for c in recipe_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        clean_title = clean_title.replace(' ', '_')[:50]  # Limit length
        filename = f"{clean_title}.json"
        
        add_debug_log(f"Generated filename: {filename}")
        
        # Create JSON content
        json_content = json.dumps(recipe_json, indent=2)
        add_debug_log(f"JSON content length: {len(json_content)} characters")
        
        # Save local copy first
        local_folder = '/Users/tycenj/Desktop/EZMeals_DB_Storage/AutomatedUploads'
        local_filepath = f"{local_folder}/{filename}"
        
        try:
            # Create directory if it doesn't exist
            import os
            os.makedirs(local_folder, exist_ok=True)
            add_debug_log(f"Created/verified local folder: {local_folder}")
            
            # Write local file
            with open(local_filepath, 'w') as f:
                f.write(json_content)
            
            add_debug_log(f"Local file saved to: {local_filepath}")
            local_success = True
            local_error = None
        except Exception as e:
            add_debug_log(f"Local save failed: {str(e)}")
            local_success = False
            local_error = str(e)
        
        # Upload to S3 using ezmeals profile
        add_debug_log("Starting S3 upload...")
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=filename,
            Body=json_content,
            ContentType='application/json'
        )
        
        add_debug_log("S3 upload completed successfully")
        return filename, True, None, local_success, local_error, local_filepath
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        add_debug_log(f"S3 ClientError - {error_code}: {str(e)}")
        if error_code == 'NoSuchBucket':
            return None, False, f"S3 bucket '{S3_BUCKET}' not found. Please check bucket name and permissions.", False, None, None
        elif error_code == 'AccessDenied':
            return None, False, f"Access denied to S3 bucket '{S3_BUCKET}'. Please check ezmeals profile permissions.", False, None, None
        else:
            return None, False, f"S3 upload failed: {str(e)}", False, None, None
    except Exception as e:
        add_debug_log(f"Unexpected error: {str(e)}")
        import traceback
        add_debug_log(f"Traceback: {traceback.format_exc()}")
        return None, False, f"Unexpected error during S3 upload: {str(e)}", False, None, None
        if error_code == 'NoSuchBucket':
            return None, False, f"S3 bucket '{S3_BUCKET}' not found. Please check bucket name and permissions.", False, None, None
        elif error_code == 'AccessDenied':
            return None, False, f"Access denied to S3 bucket '{S3_BUCKET}'. Please check ezmeals profile permissions.", False, None, None
        else:
            return None, False, f"S3 upload failed: {str(e)}", False, None, None
    except Exception as e:
        return None, False, f"Unexpected error during S3 upload: {str(e)}", False, None, None

# Create two columns
col1, col2 = st.columns([10, 10])

# Variable to store execution ARN and recipe data for later querying
if 'execution_arn' not in st.session_state:
    st.session_state['execution_arn'] = None
if 'recipe_data' not in st.session_state:
    st.session_state['recipe_data'] = None
if 'recipe_title' not in st.session_state:
    st.session_state['recipe_title'] = None

# Lambda client for URL scraper
lambda_client = boto3.client('lambda', region_name='us-west-2')

# Left column for input
with col1:
    st.header("Input Recipe")
    input_tab, url_tab = st.tabs(["📝 Paste Text", "🔗 Scrape URL"])

    with input_tab:
        recipe_text = st.text_area("Enter your recipe here:")
        if st.button("Submit", key="submit_text"):
            if recipe_text.strip() == "":
                st.error("Please enter a recipe.")
            else:
                try:
                    response = stepfunctions_client.start_execution(
                        stateMachineArn='arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2',
                        input=json.dumps({'recipe': recipe_text})
                    )
                    st.session_state['execution_arn'] = response['executionArn']
                    st.session_state['workflow_status'] = "RUNNING"
                    st.session_state['recipe_data'] = None
                    st.session_state['recipe_title'] = None
                    st.success("Recipe submitted! Execution ARN: " + response['executionArn'])
                except Exception as e:
                    st.error(f"An error occurred: {e}")

    with url_tab:
        recipe_url = st.text_input("Enter recipe URL:", placeholder="https://www.allrecipes.com/recipe/...")
        if st.button("🔗 Scrape & Process", key="submit_url"):
            if not recipe_url.strip():
                st.error("Please enter a URL.")
            elif not recipe_url.startswith('http'):
                st.error("URL must start with http:// or https://")
            else:
                try:
                    with st.spinner("Scraping recipe from URL..."):
                        add_debug_log(f"Scraping URL: {recipe_url}")
                        resp = lambda_client.invoke(
                            FunctionName='ez-recipe-url-scraper',
                            Payload=json.dumps({'url': recipe_url})
                        )
                        payload = json.loads(resp['Payload'].read())

                        if resp.get('FunctionError'):
                            raise RuntimeError(f"Lambda error: {payload.get('errorMessage', json.dumps(payload))}")

                        body = json.loads(payload['body'])
                        st.session_state['execution_arn'] = body['executionArn']
                        st.session_state['workflow_status'] = "RUNNING"
                        st.session_state['recipe_data'] = None
                        st.session_state['recipe_title'] = None

                        add_debug_log(f"Extraction method: {body['extractionMethod']}")
                        st.success(f"Recipe scraped via {body['extractionMethod']}! Processing started.")
                        st.text_area("Extracted preview:", value=body['recipePreview'], height=200, disabled=True)
                except Exception as e:
                    add_debug_log(f"URL scrape error: {str(e)}")
                    st.error(f"Failed to scrape URL: {e}")

# Right column for displaying results
with col2:
    st.header("Processing Results")

    # Check if an execution ARN is available
    if st.session_state['execution_arn']:
        # Get the status of the workflow execution
        if st.button("Check Status"):
            add_debug_log("Check Status button clicked")
            try:
                execution_response = stepfunctions_client.describe_execution(
                    executionArn=st.session_state['execution_arn']
                )
                st.session_state['workflow_status'] = execution_response['status']
                add_debug_log(f"Workflow status: {execution_response['status']}")

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

                        # Store recipe data in session state for persistence
                        st.session_state['recipe_data'] = recipe_json
                        st.session_state['qa_summary'] = summary
                        st.session_state['processing_notes'] = processing_notes
                        
                        # Extract recipe title for filename
                        if recipe_json and 'title' in recipe_json:
                            title_field = recipe_json['title']
                            if isinstance(title_field, dict) and 'S' in title_field:
                                st.session_state['recipe_title'] = title_field['S']
                            elif isinstance(title_field, str):
                                st.session_state['recipe_title'] = title_field
                            else:
                                st.session_state['recipe_title'] = "Unknown_Recipe"
                        else:
                            st.session_state['recipe_title'] = "Unknown_Recipe"
                        
                        add_debug_log(f"Recipe data stored: {st.session_state['recipe_title']}")

                    else:
                        st.error("The 'body' field is missing in stepOutput. Check the workflow configuration.")
                        # Debug information
                        st.write("Available keys in stepOutput:", list(step_output.keys()) if step_output else "No stepOutput")

                elif execution_response['status'] in ['FAILED', 'TIMED_OUT', 'ABORTED']:
                    st.error(f"Workflow ended with status: {execution_response['status']}")
                else:
                    st.info("Workflow is still running. Please wait and click 'Check Status' again.")

            except Exception as e:
                add_debug_log(f"Error checking status: {str(e)}")
                st.error(f"An error occurred while fetching the workflow status: {e}")
        
        # Display results if available (persistent across button clicks)
        if st.session_state.get('qa_summary') and st.session_state.get('recipe_data'):
            add_debug_log("Displaying persistent results")
            
            # Display the QA summary
            st.subheader("QA Summary:")
            st.write(st.session_state['qa_summary'])

            # Display processing notes if available
            if st.session_state['processing_notes']:
                st.subheader("Processing Notes:")
                for i, note in enumerate(st.session_state['processing_notes'], 1):
                    st.write(f"{i}. {note}")
            
            # Display the recipe JSON - EDITABLE
            st.subheader("Recipe JSON:")
            recipe_json = st.session_state['recipe_data']
            
            # Make JSON editable using text_area
            edited_json_str = st.text_area(
                "Edit Recipe JSON (if needed):",
                value=json.dumps(recipe_json, indent=2),
                height=400,
                help="You can edit the JSON directly here before uploading to S3"
            )
            
            # Try to parse the edited JSON to validate it
            try:
                edited_recipe_json = json.loads(edited_json_str)
                st.success("✅ JSON is valid")
                
                # Update session state with edited JSON
                st.session_state['recipe_data'] = edited_recipe_json
                
            except json.JSONDecodeError as e:
                st.error(f"❌ Invalid JSON: {e}")
                st.warning("Please fix the JSON syntax before uploading")
                edited_recipe_json = None
            
            # Add S3 Upload Section
            st.subheader("Upload to S3:")
            st.write(f"**Recipe Title:** {st.session_state['recipe_title']}")
            st.write(f"**Target Bucket:** {S3_BUCKET}")
            
            # Only show upload button if JSON is valid
            if edited_recipe_json is not None:
                if st.button("🚀 Submit Recipe to S3", type="primary"):  # Blue button (primary)
                    add_debug_log("Upload button clicked")
                    with st.spinner("Uploading recipe to S3 and saving local copy..."):
                        try:
                            add_debug_log("Starting upload process")
                            
                            result = upload_recipe_to_s3(
                                edited_recipe_json,  # Use edited JSON
                                st.session_state['recipe_title']
                            )
                            
                            add_debug_log(f"Upload result received: {len(result) if result else 0} items")
                            
                            filename, s3_success, s3_error, local_success, local_error, local_filepath = result
                            
                            if s3_success:
                                st.success(f"✅ Recipe successfully uploaded to S3!")
                                st.info(f"**Filename:** {filename}")
                                st.info(f"**S3 Location:** s3://{S3_BUCKET}/{filename}")
                                
                                # Show S3 console link
                                s3_url = f"https://us-west-1.console.aws.amazon.com/s3/object/{S3_BUCKET}?region=us-west-1&prefix={filename}"
                                st.markdown(f"[🔗 View in S3 Console]({s3_url})")
                                
                                # Show local save status
                                if local_success:
                                    st.success(f"✅ Local copy saved to: {local_filepath}")
                                else:
                                    st.warning(f"⚠️ S3 upload succeeded, but local save failed: {local_error}")
                                
                                # Mark upload as successful
                                st.session_state['upload_success'] = True
                                add_debug_log("Upload completed successfully")
                            else:
                                st.error(f"❌ Failed to upload recipe to S3: {s3_error}")
                                add_debug_log(f"Upload failed: {s3_error}")
                                
                                # Show local save status even if S3 failed
                                if local_success:
                                    st.info(f"✅ Local copy saved to: {local_filepath}")
                                elif local_error:
                                    st.error(f"❌ Local save also failed: {local_error}")
                        except Exception as e:
                            add_debug_log(f"Upload exception: {str(e)}")
                            st.error(f"❌ Unexpected error during upload: {str(e)}")
                            import traceback
                            st.error(f"Traceback: {traceback.format_exc()}")
            else:
                st.warning("⚠️ Please fix JSON syntax before uploading")
    else:
        st.write("Submit a recipe to start processing.")

# Display debug log in sidebar
display_debug_log()
