# EZmeals Recipe Creator

A recipe processing pipeline that combines URL scraping, AI-powered recipe standardization, and AWS Step Functions to automate recipe creation for the EZmeals platform.

## Overview

The Recipe Creator system takes recipes from external URLs or manual input and processes them through a multi-step pipeline:

1. **URL Scraping** — Extracts recipe data from external websites via a Lambda-based scraper
2. **Text-to-JSON Conversion** — Parses raw recipe text into structured JSON
3. **Ingredient Standardization** — Normalizes ingredient names, quantities, and units
4. **Ingredient Object Creation** — Builds structured ingredient objects for the database
5. **Side Dish Recommendations** — AI-powered side dish suggestions using Amazon Bedrock
6. **Affiliate Product Matching** — Links ingredients to affiliate products
7. **Recipe QA** — Validates the final recipe output

## Components

- `RecipeCreatorWorkflow.py` — Streamlit app for managing the recipe creation workflow
- `scrape_recipe.py` — CLI tool for scraping recipes from URLs
- `url-scraper/` — Lambda function and deployment scripts for the URL scraper service
- `StepFunctionUpdates/` — AWS Lambda functions for each Step Functions pipeline stage
- `affiliate_product_entry.py` — Tool for managing affiliate product data

## Tech Stack

- **Frontend**: Streamlit
- **Orchestration**: AWS Step Functions
- **Compute**: AWS Lambda (Python)
- **AI/ML**: Amazon Bedrock
- **Infrastructure**: AWS IAM, S3

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run RecipeCreatorWorkflow.py
```
