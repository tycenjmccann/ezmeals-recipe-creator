#!/bin/bash
set -euo pipefail

FUNCTION_NAME="ez-recipe-url-scraper"
ROLE_NAME="ez-recipe-url-scraper-role"
REGION="us-west-2"
ACCOUNT_ID="023392223961"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying $FUNCTION_NAME ==="

# Step 1: Create IAM role (skip if exists)
echo "Creating IAM role..."
if aws iam get-role --role-name "$ROLE_NAME" 2>/dev/null; then
    echo "Role already exists, skipping creation."
else
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file://"$SCRIPT_DIR/trust-policy.json" \
        --description "Role for ez-recipe-url-scraper Lambda"
    echo "Role created. Waiting for propagation..."
    sleep 10
fi

# Step 2: Attach policies
echo "Attaching policies..."
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" 2>/dev/null || true

# Inline policy for Step Functions access
aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "StepFunctionsStartExecution" \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "states:StartExecution",
                "Resource": "arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2"
            }
        ]
    }'

echo "Policies attached."

# Step 3: Package Lambda
echo "Packaging Lambda function..."
cd "$SCRIPT_DIR"
zip -j /tmp/ez-recipe-url-scraper.zip lambda_function.py

# Step 4: Create or update Lambda function
echo "Deploying Lambda function..."
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file fileb:///tmp/ez-recipe-url-scraper.zip \
        --region "$REGION"
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --runtime python3.13 \
        --handler lambda_function.lambda_handler \
        --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}" \
        --zip-file fileb:///tmp/ez-recipe-url-scraper.zip \
        --timeout 60 \
        --memory-size 256 \
        --region "$REGION" \
        --description "Scrapes recipe URLs and triggers ez-recipe-creator-V2 Step Functions"
fi

echo ""
echo "=== Deployment complete ==="
echo "Function: $FUNCTION_NAME"
echo "Region: $REGION"
echo "Test with: aws lambda invoke --function-name $FUNCTION_NAME --payload '{\"url\": \"https://www.allrecipes.com/recipe/10813/best-chocolate-chip-cookies/\"}' --region $REGION /tmp/scraper-output.json && cat /tmp/scraper-output.json"
