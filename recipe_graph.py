"""
EZmeals Recipe Creator - Agents as Tools Pattern
Orchestrator agent calls each specialist as a @tool, integrates all results.
"""

from strands import Agent, tool
from strands_tools import http_request, journal
import json
import sys

# Import our custom tools
from strands_agents_recipe_creator import (
    scrape_recipe_url, validate_recipe_json,
    get_available_sides, get_available_products,
    get_standardized_ingredients,
    generate_recipe_images, publish_recipe,
    EZMEAL_STYLE, VALID_CATEGORIES, CUISINE_TYPES,
)

# ============================================================================
# SPECIALIST AGENTS WRAPPED AS @TOOLS
# ============================================================================

@tool
def scrape_recipe(url: str) -> str:
    """Scrape a recipe URL to extract recipe text and download the main image.
    Args:
        url: Recipe URL to scrape
    """
    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[scrape_recipe_url],
        system_prompt="You receive a URL. Call scrape_recipe_url and return the COMPLETE extracted recipe text verbatim — every ingredient with exact quantities, every instruction step word-for-word, and the image path. Do NOT summarize or paraphrase. Return the full text exactly as extracted."
    )
    result = agent(f"Scrape this recipe URL: {url}")
    return str(result)

@tool
def chef_review(recipe_text: str) -> str:
    """Culinary review: analyze recipe quality, compare to web sources, optimize.
    Args:
        recipe_text: The scraped recipe text to review
    """
    agent = Agent(
        model="us.anthropic.claude-opus-4-6-v1",
        system_prompt="""You are a professional chef reviewing a scraped recipe. Your job:
1. ANALYZE: Are ratios sensible? Times realistic? Steps complete? Safety covered?
2. OPTIMIZE: Fix missing steps, adjust off ratios, add safety notes. Don't change the dish's character.
3. ENSURE: All ingredients have quantities. All instructions reference specific amounts. Beginner-friendly language.

Output: CULINARY ASSESSMENT, OPTIMIZED RECIPE (full text with ALL quantities preserved and improvements noted), CHANGES MADE."""
    )
    result = agent(f"Review and optimize this recipe:\n\n{recipe_text}")
    return str(result)

@tool
def convert_to_json(recipe_text: str, recipe_id: str) -> str:
    """Convert recipe text to DynamoDB-compatible JSON with validation.
    Args:
        recipe_text: The raw recipe text
        recipe_id: Unique ID for the recipe
    """
    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[validate_recipe_json],
        system_prompt=open('/Users/tycenj/Desktop/EZmeals_Backlog/RecipeCreator/prompts/step1_prompt.txt').read() if False else """
Convert detailed recipe information into a JSON file adhering to the specified schema and formatting compatible with DynamoDB's requirements.
Use the correct data type indicators ("S", "N", "BOOL", "L", "M").

Required Fields:
id ("S"), title ("S"), dishType ("S": "main" or "side"), primary ("BOOL"), baseMainId ("S": ""),
imageURL ("S": menu-item-images/Recipe_Name.jpg), imageThumbURL ("S": menu-item-images/Recipe_Name_thumbnail.jpg),
description ("S"), link ("S"), prepTime ("N"), cookTime ("N"), rating ("N": 5), servings ("S"),
cuisineType ("S": one of "Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"),
isQuick ("BOOL": true if 0-30 min), isBalanced ("BOOL": true if 31-60 min), isGourmet ("BOOL": true if >60 min),
ingredients ("L"), ingredient_objects ("L": []), instructions ("L"), notes ("L"),
recommendedSides ("L": []), includedSides ("L": []), comboIndex ("M": {}), products ("L": []),
glutenFree ("BOOL": true), vegetarian ("BOOL"), slowCook ("BOOL"), instaPot ("BOOL"), flagged ("BOOL": false).

Instructions must: begin with imperative verb, include ingredient quantities, be beginner-friendly.
Ingredients must use fractions (1/2 not 0.5), no special characters, standardized units.
Notes: include GF substitutions, dietary variations only.

After generating JSON, CALL validate_recipe_json to auto-fix any issues. Use the corrected JSON.
Only return the final JSON, no additional text.
"""
    )
    result = agent(f"Recipe Details:\n{recipe_text}\n\nUse recipe ID: {recipe_id}")
    return str(result)

@tool
def standardize_ingredients(recipe_json: str) -> str:
    """Standardize ingredient names and units in the recipe.
    Args:
        recipe_json: The recipe JSON string
    """
    agent = Agent(
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tools=[get_standardized_ingredients],
        system_prompt="""Standardize ONLY ingredient names and units.

FIRST: Call get_standardized_ingredients with a comma-separated list of core ingredient names from the recipe. Use the returned standardized names as your reference.

CRITICAL RULES:
1. Capitalize core ingredient names (e.g., "soy sauce" → "Soy Sauce")
2. Standardize units (lbs → pounds, tsp → teaspoon, tbsp → tablespoon)
3. PRESERVE original quantities exactly
4. PRESERVE ALL preparation instructions exactly as written
5. If no exact match exists, leave unchanged
Return the updated ingredients list and a summary of changes made."""
    )
    result = agent(f"Standardize the ingredients in this recipe:\n{recipe_json}")
    return str(result)

@tool
def create_ingredient_objects(recipe_json: str) -> str:
    """Parse ingredients into structured DynamoDB objects.
    Args:
        recipe_json: The recipe JSON string
    """
    agent = Agent(
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        system_prompt="""Parse ingredients into structured objects. For each ingredient create:
{"M": {"ingredient_name": {"S": "Name"}, "category": {"S": "Category"}, "quantity": {"S": "Amount"}, "unit": {"S": "Unit"}, "note": {"S": "Prep notes"}, "affiliate_link": {"S": ""}}}

Categories MUST be one of: Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods
Move descriptors (large, fresh, chopped, diced) to note field. Capitalize ingredient names.
Return ONLY the ingredient_objects JSON structure."""
    )
    result = agent(f"Parse these ingredients into structured objects:\n{recipe_json}")
    return str(result)

@tool
def recommend_sides(recipe_title: str, recipe_description: str, recipe_ingredients: str, recipe_cuisine: str) -> str:
    """Recommend side dishes for a recipe from our catalog and identify gaps.
    Args:
        recipe_title: Name of the recipe
        recipe_description: Description of the recipe
        recipe_ingredients: Ingredients list
        recipe_cuisine: Cuisine type
    """
    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[get_available_sides],
        system_prompt="""You recommend side dishes. FIRST call get_available_sides to get our catalog.
Then analyze the recipe and recommend 3-6 sides that complement it well.
Also search the web for popular pairings and note any sides we should create.
Return: RECOMMENDED_SIDES: [list of IDs] and RECOMMENDED NEW SIDES TO CREATE: [list with reasons]."""
    )
    result = agent(f"Recommend sides for: {recipe_title}\nCuisine: {recipe_cuisine}\nDescription: {recipe_description}\nIngredients: {recipe_ingredients}")
    return str(result)

@tool
def recommend_products(recipe_title: str, recipe_description: str, recipe_instructions: str) -> str:
    """Recommend affiliate products for a recipe from our catalog and identify gaps.
    Args:
        recipe_title: Name of the recipe
        recipe_description: Description of the recipe
        recipe_instructions: Cooking instructions
    """
    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[get_available_products],
        system_prompt="""You recommend NON-FOOD affiliate products. FIRST call get_available_products to get our catalog.
Then analyze the recipe for tools/equipment needed. Also search the web for common tools.
Return: RECOMMENDED_PRODUCTS: [list of IDs] and RECOMMENDED NEW PRODUCTS TO ADD: [list with reasons]."""
    )
    result = agent(f"Recommend products for: {recipe_title}\nDescription: {recipe_description}\nInstructions: {recipe_instructions}")
    return str(result)

@tool
def qa_review(original_recipe: str, final_json: str, processing_summary: str) -> str:
    """Run QA validation checklist on the final assembled recipe.
    Args:
        original_recipe: Original recipe text
        final_json: The final assembled recipe JSON
        processing_summary: Summary of what each agent did
    """
    agent = Agent(
        model="us.anthropic.claude-opus-4-6-v1",
        tools=[],
        system_prompt="""You are QA for the ezMeals app. Validate the final recipe JSON against this checklist:

1. INSTRUCTIONS: Every step has quantities, imperative verbs, beginner-friendly
2. INGREDIENTS: Fractions (not decimals), standardized units, no special chars
3. INGREDIENT OBJECTS: Populated, correct categories (Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods)
4. METADATA: cuisineType from [Global Cuisines, American, Asian, Indian, Italian, Latin, Soups & Stews], time flags correct (0-30=quick, 31-60=balanced, >60=gourmet), imageURL format correct
5. SIDES: recommendedSides populated with valid IDs
6. PRODUCTS: products populated with non-food item IDs
7. NOTES: GF substitutions included

Output: OVERALL QUALITY (PUBLISH or REVIEW NEEDED), checklist results, and specific fixes required."""
    )
    result = agent(f"Original Recipe:\n{original_recipe}\n\nFinal JSON:\n{final_json}\n\nProcessing Summary:\n{processing_summary}")
    return str(result)


# ============================================================================
# ORCHESTRATOR - calls each specialist, integrates results
# ============================================================================

orchestrator = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[
        scrape_recipe, chef_review, convert_to_json, standardize_ingredients,
        create_ingredient_objects, recommend_sides, recommend_products,
        qa_review, generate_recipe_images, publish_recipe
    ],
    system_prompt="""You are the EZmeals Recipe Creator orchestrator. Given a recipe URL, you manage the complete pipeline:

1. Call scrape_recipe with the URL to get recipe text + image path
2. Call chef_review with the recipe text — get culinary analysis, web comparison, and optimized recipe
3. Call convert_to_json with the OPTIMIZED recipe text (from chef review) to get DynamoDB JSON
4. Call standardize_ingredients to fix ingredient names/units
5. Call create_ingredient_objects to build structured ingredient objects
6. Call recommend_sides to get side dish recommendations
7. Call recommend_products to get affiliate product recommendations
8. ASSEMBLE the final JSON by merging all results: update ingredients, ingredient_objects, recommendedSides, and products in the recipe JSON
9. Call qa_review with the original text and final assembled JSON. If QA identifies critical fixes, apply them. If QA fails, route back to the responsible agent.
10. Call generate_recipe_images with the scraped image to create hero (16:9) and thumbnail (1:1)
11. Call publish_recipe with the final JSON, hero image path, thumbnail image path, and recipe ID to upload to S3 + DynamoDB and save locally

CRITICAL: After each specialist returns, YOU integrate their output into the recipe JSON before moving to the next step. You are the single source of truth for the recipe state.
CRITICAL: The pipeline always publishes. If QA fails, fix the issues and re-run QA until it passes, then publish."""
)


if __name__ == "__main__":
    import os
    os.environ.setdefault('AWS_DEFAULT_REGION', 'us-west-2')

    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.iankewks.com/thai-crying-tiger-steak/"

    print(f"🔗 EZmeals Recipe Creator — Agents as Tools")
    print(f"URL: {url}")
    print("=" * 80)

    result = orchestrator(f"Process this recipe URL: {url}")

    print("\n" + "=" * 80)
    print("🎉 PIPELINE COMPLETE")
    print(str(result)[:2000])
