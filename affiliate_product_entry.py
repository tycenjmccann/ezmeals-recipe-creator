

import streamlit as st
import boto3
import uuid
from datetime import datetime, timezone
import botocore

# Set the page layout to wide
st.set_page_config(layout="wide")

# Set the title of the app
st.title("Affiliate Product Entry")

# AWS DynamoDB resource
try:
    session = boto3.Session(profile_name='ezmeals')
    sts = session.client('sts')
    account_info = sts.get_caller_identity()
    st.sidebar.success(f"Connected to AWS Account: {account_info['Account']}")
    
    dynamodb = session.resource('dynamodb', region_name='us-west-1')
    
    # Test DynamoDB connection
    tables = list(dynamodb.tables.all())
    st.sidebar.success(f"Successfully connected to DynamoDB. Found {len(tables)} tables.")
except botocore.exceptions.ProfileNotFound:
    st.sidebar.error("AWS profile not found. Please check your profile name.")
except botocore.exceptions.ClientError as e:
    st.sidebar.error(f"AWS Error: {str(e)}")
except Exception as e:
    st.sidebar.error(f"An unexpected error occurred: {str(e)}")

# DynamoDB table
table_name = 'AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev'
try:
    table = dynamodb.Table(table_name)
    table.table_status  # Ensure table exists
    st.sidebar.success(f"Successfully connected to table: {table_name}")
except botocore.exceptions.ClientError as e:
    if e.response['Error']['Code'] == 'ResourceNotFoundException':
        st.sidebar.error(f"Table {table_name} not found. Please check the table name and region.")
    else:
        st.sidebar.error(f"Error accessing table: {str(e)}")

# Create two columns
col1, col2 = st.columns([1, 1])

# Function to update an item in DynamoDB
def update_item(item_id, updated_data):
    try:
        update_expression = "SET "
        expression_attribute_values = {}
        for key, value in updated_data.items():
            update_expression += f"{key} = :{key}, "
            expression_attribute_values[f":{key}"] = value
        update_expression += "updatedAt = :updatedAt"
        expression_attribute_values[":updatedAt"] = datetime.now(timezone.utc).isoformat(timespec='seconds').replace("+00:00", "Z")
        
        table.update_item(
            Key={'id': item_id},
            UpdateExpression=update_expression.rstrip(", "),  # Remove trailing comma
            ExpressionAttributeValues=expression_attribute_values
        )
        return True
    except Exception as e:
        st.error(f"An error occurred while updating: {e}")
        return False

# Left column for input
with col1:
    st.header("Enter Product Information")
    productName = st.text_input("Product Name")
    description = st.text_input("Description")
    inAppText = st.text_input("In-App Text*")
    link = st.text_input("Link*")
    imageURL = st.text_input("Image URL")
    usedInMenuItem = st.text_input("Used In Menu Item (comma-separated)")
    linkLocation = st.text_input("Link Location (comma-separated)")

    if st.button("Submit"):
        if not inAppText.strip():
            st.error("In-App Text is required.")
        elif not link.strip():
            st.error("Link is required.")
        else:
            item = {
                'id': str(uuid.uuid4()),
                'productName': productName,
                'description': description,
                'inAppText': inAppText,
                'link': link,
                'imageURL': imageURL,
                'usedInMenuItem': [s.strip() for s in usedInMenuItem.split(",")] if usedInMenuItem else [],
                'linkLocation': [s.strip() for s in linkLocation.split(",")] if linkLocation else [],
                'createdAt': datetime.now(timezone.utc).isoformat(timespec='seconds').replace("+00:00", "Z"),
                'updatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds').replace("+00:00", "Z")
            }
            try:
                table.put_item(Item=item)
                st.success("Affiliate product added successfully!")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"An error occurred: {e}")

# Right column for displaying and editing existing products
with col2:
    st.header("Existing Products")
    
    # Add search box at the top of the existing products section
    search_query = st.text_input("Search products", key="search_box")
    
    try:
        response = table.scan()
        items = response.get('Items', [])
        if items:
            sorted_items = sorted(items, key=lambda x: x.get('createdAt', ''), reverse=True)
            
            # Filter products based on search query
            if search_query:
                filtered_items = []
                for product in sorted_items:
                    # Search in product name, description, and in-app text
                    product_name = product.get('productName', '').lower()
                    description = product.get('description', '').lower()
                    in_app_text = product.get('inAppText', '').lower()
                    used_in_menu = ','.join(product.get('usedInMenuItem', [])).lower()
                    
                    search_term = search_query.lower()
                    if (search_term in product_name or 
                        search_term in description or 
                        search_term in in_app_text or
                        search_term in used_in_menu):
                        filtered_items.append(product)
                display_items = filtered_items
            else:
                display_items = sorted_items
                
            # Display count of filtered results
            if search_query:
                st.write(f"Found {len(display_items)} matching products")
            
            for product in display_items:
                with st.expander(f"Edit: {product.get('productName', '')}", expanded=False):
                    product_id = product.get('id', 'N/A')

                    # Display ID with a built-in copy button
                    st.markdown("**Product ID:**")
                    st.code(product_id, language="")  # This provides a built-in copy button

                    # Editable fields
                    edited_product = {}
                    edited_product['productName'] = st.text_input("Product Name", product.get('productName', ''), key=f"name_{product['id']}")
                    edited_product['description'] = st.text_input("Description", product.get('description', ''), key=f"desc_{product['id']}")
                    edited_product['inAppText'] = st.text_input("In-App Text", product.get('inAppText', ''), key=f"inapp_{product['id']}")
                    edited_product['link'] = st.text_input("Link", product.get('link', ''), key=f"link_{product['id']}")
                    edited_product['imageURL'] = st.text_input("Image URL", product.get('imageURL', ''), key=f"img_{product['id']}")
                    edited_product['usedInMenuItem'] = st.text_input("Used In Menu Item (comma-separated)", 
                                                                     ','.join(product.get('usedInMenuItem', [])), key=f"menu_{product['id']}")
                    edited_product['linkLocation'] = st.text_input("Link Location (comma-separated)", 
                                                                   ','.join(product.get('linkLocation', [])), key=f"loc_{product['id']}")

                    if st.button("Update", key=f"update_{product['id']}"):
                        edited_product['usedInMenuItem'] = [s.strip() for s in edited_product['usedInMenuItem'].split(",") if s.strip()]
                        edited_product['linkLocation'] = [s.strip() for s in edited_product['linkLocation'].split(",") if s.strip()]
    
                        if update_item(product['id'], edited_product):
                            st.success("Product updated successfully!")
                            st.rerun()  # Correct replacement for st.experimental_rerun()


    except Exception as e:
        st.error(f"An error occurred: {e}")




