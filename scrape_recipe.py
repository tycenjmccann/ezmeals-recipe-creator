#!/usr/bin/env python3
"""CLI tool to scrape a recipe URL and trigger the Recipe Creator workflow."""
import argparse
import json
import sys
import time
import boto3

FUNCTION_NAME = 'ez-recipe-url-scraper'
STATE_MACHINE_ARN = 'arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2'
REGION = 'us-west-2'


def invoke_scraper(url):
    """Invoke the URL scraper Lambda and return the response."""
    client = boto3.client('lambda', region_name=REGION)
    response = client.invoke(
        FunctionName=FUNCTION_NAME,
        Payload=json.dumps({'url': url})
    )
    payload = json.loads(response['Payload'].read())

    if 'errorMessage' in payload:
        raise RuntimeError(f"Lambda error: {payload['errorMessage']}")

    if response.get('FunctionError'):
        raise RuntimeError(f"Lambda function error: {json.dumps(payload, indent=2)}")

    body = json.loads(payload['body'])
    return body


def poll_execution(execution_arn):
    """Poll Step Functions execution until complete."""
    sfn = boto3.client('stepfunctions', region_name=REGION)
    print("Polling execution status...")

    while True:
        resp = sfn.describe_execution(executionArn=execution_arn)
        status = resp['status']
        print(f"  Status: {status}")

        if status == 'SUCCEEDED':
            output = json.loads(resp.get('output', '{}'))
            return output
        elif status in ('FAILED', 'TIMED_OUT', 'ABORTED'):
            raise RuntimeError(f"Execution {status}: {resp.get('error', 'unknown')}")

        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description='Scrape a recipe URL and process through EZ Recipe Creator')
    parser.add_argument('url', help='Recipe URL to scrape')
    parser.add_argument('--wait', action='store_true', help='Wait for Step Functions execution to complete')
    args = parser.parse_args()

    print(f"Scraping: {args.url}")
    result = invoke_scraper(args.url)

    print(f"\nExtraction method: {result['extractionMethod']}")
    print(f"Execution ARN: {result['executionArn']}")
    print(f"\nRecipe preview:\n{result['recipePreview']}")

    if args.wait:
        print(f"\n{'='*60}")
        output = poll_execution(result['executionArn'])
        step_output = output.get('stepOutput', {})
        if 'body' in step_output:
            body = json.loads(step_output['body'])
            print(f"\nQA Summary:\n{body.get('summary', 'N/A')}")
            print(f"\nProcessing Notes:")
            for note in body.get('processingNotes', []):
                print(f"  - {note}")
        else:
            print(json.dumps(output, indent=2))
    else:
        print(f"\nTrack progress in AWS Console or run with --wait flag")


if __name__ == '__main__':
    main()
