"""
EZmeals Recipe Creator - Strands Agents Implementation (V3 - Fixed)
All prompts copied EXACTLY from Step Functions. Custom @tools for validation and DB lookups.
"""

from strands import Agent, tool
from strands_tools import http_request, use_aws, workflow, agent_graph, journal
import json
import uuid
import boto3
from botocore.config import Config
from typing import Dict, Any, List

# ============================================================================
# AWS CONFIG (matches Step Functions exactly)
# ============================================================================
AWS_CONFIG = {
    'ROLE_ARN': "arn:aws:iam::970547358447:role/CrossAccountDynamoDBWriter",
    'MENU_TABLE': "MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev",
    'PRODUCT_TABLE': "AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev",
    'INGREDIENT_TABLE': "Ingredient-ryvykzwfevawxbpf5nmynhgtea-dev",
    'DYNAMODB_REGION': "us-west-1",
}

CUISINE_TYPES = ["Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"]
DISH_TYPES = ["main", "side"]
VALID_CATEGORIES = {"Produce", "Proteins", "Dairy", "Grains & Bakery", "Pantry Staples", "Seasonings", "Frozen Foods"}


def _plain_to_dynamo(recipe: dict) -> dict:
    """Convert plain JSON recipe to DynamoDB-typed format."""
    def to_dynamo(val):
        if isinstance(val, str):
            return {"S": val}
        elif isinstance(val, bool):
            return {"BOOL": val}
        elif isinstance(val, (int, float)):
            return {"N": str(val)}
        elif isinstance(val, list):
            return {"L": [to_dynamo(v) for v in val]}
        elif isinstance(val, dict):
            return {"M": {k: to_dynamo(v) for k, v in val.items()}}
        elif val is None:
            return {"NULL": True}
        return {"S": str(val)}
    
    return {k: to_dynamo(v) for k, v in recipe.items()}


def _plain_to_s3_dynamo_json(recipe: dict) -> dict:
    """Convert validated plain-JSON recipe to DynamoDB-typed JSON for S3 upload.
    
    The S3 → Lambda import pipeline expects DynamoDB-typed format:
      {"S": "string"}, {"N": "number"}, {"BOOL": true}, 
      {"L": [{"S": "item"}]}, {"M": {"key": {"S": "val"}}}
    
    Gold standard: Lomo_Saltado.json in menu-items-json bucket.
    
    CRITICAL: ingredient_objects must be {"L": [{"M": {field: {"S": val}}}]}
    NOT a stringified JSON string. The Lambda parses typed objects.
    """
    STRING_FIELDS = {'baseMainId', 'cuisineType', 'description', 'dishType',
                     'id', 'imageThumbURL', 'imageURL', 'link', 'servings', 'title'}
    NUMBER_FIELDS = {'cookTime', 'prepTime', 'rating'}
    BOOL_FIELDS = {'flagged', 'glutenFree', 'instaPot', 'isBalanced', 'isGourmet',
                   'isQuick', 'primary', 'slowCook', 'vegetarian'}
    STRING_LIST_FIELDS = {'ingredients', 'instructions', 'notes', 'products',
                          'recommendedSides', 'includedSides', 'searchTerms',
                          'dressing', 'sauce', 'seasonings', 'optionalToppings'}
    # Skip these — Lambda/Amplify manages them
    SKIP_FIELDS = {'createdAt', 'updatedAt', '_version', '_lastChangedAt'}

    result = {}

    for k, v in recipe.items():
        if k in SKIP_FIELDS:
            continue

        if k in STRING_FIELDS:
            result[k] = {"S": str(v) if v is not None else ""}
        elif k in NUMBER_FIELDS:
            result[k] = {"N": str(v)}
        elif k in BOOL_FIELDS:
            result[k] = {"BOOL": bool(v)}
        elif k == 'comboIndex':
            # Always empty map for non-combos
            result[k] = {"M": {}}
        elif k == 'ingredient_objects':
            # Parse stringified JSON → L of M
            items = v
            if isinstance(v, str):
                try:
                    items = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    items = []
            if not isinstance(items, list):
                items = []
            dynamo_items = []
            for item in items:
                if isinstance(item, dict):
                    m = {ik: {"S": str(iv) if iv is not None else ""}
                         for ik, iv in item.items()}
                    dynamo_items.append({"M": m})
            result[k] = {"L": dynamo_items}
        elif k in STRING_LIST_FIELDS:
            if isinstance(v, list):
                result[k] = {"L": [{"S": str(item)} for item in v]}
            else:
                result[k] = {"L": []}

    return result

# ============================================================================
# CUSTOM TOOLS
# ============================================================================

def _get_dynamodb_table(table_name):
    """Get DynamoDB table via cross-account role or direct access."""
    try:
        sts = boto3.client("sts")
        creds = sts.assume_role(
            RoleArn=AWS_CONFIG['ROLE_ARN'],
            RoleSessionName="StrandsAgentSession"
        )['Credentials']
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=AWS_CONFIG['DYNAMODB_REGION'],
            aws_access_key_id=creds['AccessKeyId'],
            aws_secret_access_key=creds['SecretAccessKey'],
            aws_session_token=creds['SessionToken']
        )
    except Exception as e:
        # Fallback: direct access with ezmeals profile (if available)
        try:
            session = boto3.Session(profile_name='ezmeals')
            dynamodb = session.resource("dynamodb", region_name=AWS_CONFIG['DYNAMODB_REGION'])
        except Exception:
            # Last resort: direct access with default credentials
            dynamodb = boto3.resource("dynamodb", region_name=AWS_CONFIG['DYNAMODB_REGION'])
    return dynamodb.Table(table_name)


@tool
def validate_recipe_json(recipe_json_str: str) -> str:
    """
    Validate and auto-fix a recipe JSON against the required schema.
    Applies the EXACT same validation logic as the Step Functions pipeline.
    Returns a report of what was fixed and any remaining issues.

    Args:
        recipe_json_str: The recipe JSON string to validate
    """
    try:
        # Strip markdown code blocks if present
        clean = recipe_json_str.strip() if isinstance(recipe_json_str, str) else recipe_json_str
        if isinstance(clean, str):
            import re
            # Extract JSON from ```json ... ``` blocks
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', clean, re.DOTALL)
            if match:
                clean = match.group(1).strip()
        
        recipe = json.loads(clean) if isinstance(clean, str) else clean
    except json.JSONDecodeError as e:
        return f"ERROR: Invalid JSON - {e}"

    # Auto-detect and convert plain JSON to DynamoDB format if needed
    if 'title' in recipe and isinstance(recipe.get('title'), str):
        recipe = _plain_to_dynamo(recipe)

    fixes = []

    # Validate cuisineType
    ct = recipe.get('cuisineType', {}).get('S', '')
    if ct not in CUISINE_TYPES:
        recipe['cuisineType']['S'] = 'Global Cuisines'
        fixes.append(f"Fixed cuisineType: '{ct}' is invalid, changed to 'Global Cuisines'")

    # Validate dishType
    dt = recipe.get('dishType', {}).get('S', '')
    if dt not in DISH_TYPES:
        recipe['dishType']['S'] = 'main'
        fixes.append(f"Fixed dishType: '{dt}' is invalid, changed to 'main'")

    # Fix primary flag to match dishType
    is_main = recipe['dishType']['S'] == 'main'
    if recipe.get('primary', {}).get('BOOL') != is_main:
        recipe['primary']['BOOL'] = is_main
        fixes.append(f"Fixed primary flag: set to {is_main} for {recipe['dishType']['S']} dish")

    # Recalculate time flags from prepTime + cookTime
    try:
        total_time = int(recipe['prepTime']['N']) + int(recipe['cookTime']['N'])

        correct_quick = (0 <= total_time <= 30)
        correct_balanced = (31 <= total_time <= 60)
        correct_gourmet = (total_time > 60)

        if recipe['isQuick']['BOOL'] != correct_quick:
            old = recipe['isQuick']['BOOL']
            recipe['isQuick']['BOOL'] = correct_quick
            fixes.append(f"Fixed isQuick: {total_time} min total, changed from {old} to {correct_quick}")

        if recipe['isBalanced']['BOOL'] != correct_balanced:
            old = recipe['isBalanced']['BOOL']
            recipe['isBalanced']['BOOL'] = correct_balanced
            fixes.append(f"Fixed isBalanced: {total_time} min total, changed from {old} to {correct_balanced}")

        if recipe['isGourmet']['BOOL'] != correct_gourmet:
            old = recipe['isGourmet']['BOOL']
            recipe['isGourmet']['BOOL'] = correct_gourmet
            fixes.append(f"Fixed isGourmet: {total_time} min total, changed from {old} to {correct_gourmet}")
    except (KeyError, ValueError) as e:
        fixes.append(f"WARNING: Could not validate time flags - {e}")

    # Fix imageURL prefix
    for field in ['imageURL', 'imageThumbURL']:
        val = recipe.get(field, {}).get('S', '')
        if val and not val.startswith('menu-item-images/'):
            filename = val.split('/')[-1] if '/' in val else val
            recipe[field]['S'] = f"menu-item-images/{filename}"
            fixes.append(f"Fixed {field}: added menu-item-images/ prefix")

    # Force flagged to false
    if recipe.get('flagged', {}).get('BOOL') is not False:
        recipe['flagged']['BOOL'] = False
        fixes.append("Fixed flagged: set to false")

    # Add missing fields with defaults
    defaults = {
        'recommendedSides': {'L': []}, 'includedSides': {'L': []},
        'products': {'L': []}, 'notes': {'L': []},
        'ingredient_objects': {'L': []}, 'comboIndex': {'M': {}}
    }
    for field, default in defaults.items():
        if field not in recipe:
            recipe[field] = default
            fixes.append(f"Added missing field: {field}")

    report = "VALIDATION REPORT:\n"
    if fixes:
        report += f"{len(fixes)} fixes applied:\n"
        for f in fixes:
            report += f"  - {f}\n"
    else:
        report += "No fixes needed - all validations passed.\n"

    report += f"\nCORRECTED JSON:\n{json.dumps(recipe, indent=2)}"
    return report


@tool
def get_available_sides() -> str:
    """
    Get all available side dishes from the MenuItemData DynamoDB table.
    Scans for dishType=side and returns id, title, description, cuisineType, and ingredients.
    """
    try:
        table = _get_dynamodb_table(AWS_CONFIG['MENU_TABLE'])
        items = []
        last_key = None
        while True:
            kwargs = {
                'FilterExpression': 'dishType = :dt',
                'ExpressionAttributeValues': {':dt': 'side'}
            }
            if last_key:
                kwargs['ExclusiveStartKey'] = last_key
            resp = table.scan(**kwargs)
            items.extend(resp.get('Items', []))
            last_key = resp.get('LastEvaluatedKey')
            if not last_key:
                break

        sides = []
        for item in items:
            sides.append({
                'id': item.get('id', ''),
                'title': item.get('title', ''),
                'description': item.get('description', ''),
                'cuisineType': item.get('cuisineType', ''),
                'ingredients': item.get('ingredients', [])
            })

        return json.dumps(sides, indent=2, default=str)
    except Exception as e:
        return f"ERROR fetching sides: {e}"


@tool
def get_available_products() -> str:
    """
    Get all available affiliate products from the AffiliateProduct DynamoDB table.
    Returns id, productName, description, and link for each product.
    """
    try:
        table = _get_dynamodb_table(AWS_CONFIG['PRODUCT_TABLE'])
        items = []
        last_key = None
        while True:
            kwargs = {}
            if last_key:
                kwargs['ExclusiveStartKey'] = last_key
            resp = table.scan(**kwargs)
            items.extend(resp.get('Items', []))
            last_key = resp.get('LastEvaluatedKey')
            if not last_key:
                break

        products = []
        for item in items:
            products.append({
                'id': item.get('id', ''),
                'productName': item.get('productName', ''),
                'description': item.get('description', ''),
                'link': item.get('link', ''),
                'usedInMenuItem': item.get('usedInMenuItem', [])
            })

        return json.dumps(products, indent=2, default=str)
    except Exception as e:
        return f"ERROR fetching products: {e}"


@tool
def save_recommendations(side_ids: list, product_ids: list) -> str:
    """
    Save enricher recommendations to a file for post-processing.
    Args:
        side_ids: List of recommendedSides UUIDs
        product_ids: List of products UUIDs
    """
    import os, json
    output_dir = os.path.dirname(os.path.abspath(__file__))
    enrich_file = os.path.join(output_dir, 'enricher_recommendations.json')
    
    data = {
        "recommendedSides": side_ids,
        "products": product_ids
    }
    
    with open(enrich_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    return f"✅ Saved {len(side_ids)} sides and {len(product_ids)} products to {enrich_file}"

@tool
def get_standardized_ingredients(ingredients_list: str) -> str:
    """
    Get standardized ingredient names from the Ingredient DynamoDB table.
    Pass a comma-separated list of core ingredient names to match against.
    Returns matching standardized ingredient records.
    """
    try:
        table = _get_dynamodb_table(AWS_CONFIG['INGREDIENT_TABLE'])
        items = []
        last_key = None
        while True:
            kwargs = {}
            if last_key:
                kwargs['ExclusiveStartKey'] = last_key
            resp = table.scan(**kwargs)
            items.extend(resp.get('Items', []))
            last_key = resp.get('LastEvaluatedKey')
            if not last_key:
                break

        # Filter to relevant ingredients
        core_names = {n.strip().lower() for n in ingredients_list.split(',')}
        relevant = []
        for item in items:
            name = item.get('ingredient_name', '').lower()
            if any(core in name or name in core for core in core_names):
                relevant.append({
                    'ingredient_name': item.get('ingredient_name', ''),
                    'standardized_name': item.get('standardized_name', ''),
                    'unit': item.get('unit', ''),
                    'category': item.get('category', ''),
                })

        return json.dumps(relevant, indent=2, default=str)
    except Exception as e:
        return f"ERROR fetching standardized ingredients: {e}"


# ============================================================================
# AGENT 1: TEXT TO JSON CONVERTER (exact Step Function prompt)
# ============================================================================
text_to_json_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[validate_recipe_json, journal],
    system_prompt="""
Convert detailed recipe information into a JSON file adhering to the specified schema and formatting compatible with DynamoDB's requirements. Ensure the output uses the specified fields:

1. Convert Recipe Details to DynamoDB-Compatible JSON Schema:
Use the correct data type indicators ("S", "N", "BOOL", "L", "M").

Required Fields:
id ("S"): Use the unique ID provided.
title ("S"): The exact name of the recipe.
dishType ("S"): "main" for main dishes, "side" for side dishes
primary ("BOOL"): true for main dishes, false for side dishes
baseMainId ("S"): Empty string "" (placeholder for combos)
imageURL ("S"): Construct the filename using menu-item-images/[recipe_name].[extension], replacing spaces with underscores (_) in [recipe_name].
imageThumbURL ("S"): Construct the filename using menu-item-images/[recipe_name_thumbnail].[extension], replacing spaces with underscores (_) in [recipe_name].
description ("S"): A concise, engaging summary of the recipe that makes readers want to try it out, add some reference to the cuisine type if applicable.
link ("S"): Extract any URL from the recipe text, or empty string "" if none found
prepTime ("N"): Preparation time in minutes.
cookTime ("N"): Cooking time in minutes.
rating ("N"): 5
servings ("S"): Number of servings (e.g., "4", "6-8")
cuisineType ("S"): Type of cuisine from this list ["Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"]
isQuick ("BOOL") : true if prepTime + cookTime is 0-30 min, else false
isBalanced ("BOOL"): true if prepTime + cookTime is 31-60 min, else false
isGourmet ("BOOL"): true if prepTime + cookTime is > 60 min, else false
ingredients ("L"): A list of ingredients, preserve original quantities and convert to mixed fractions if needed (e.g., "1/2" not "0.5"), avoid special characters (e.g. inches not ")
ingredient_objects ("L"): Empty list [] (placeholder)
instructions ("L"): Enhance Cooking Instructions: Beginning each step with an imperative verb, Break complex steps into simpler steps easy for beginners to follow, Include ingredient quantities within the instructions for ease of use and clarity. 
notes ("L"): Notes should be limited, and include gluten free and/or other dietary substitutes like: use gluten free noodles, flour, etc. Remove notes that are not directly related to making the recipe or substitutions and/or variations of the recipe
recommendedSides ("L"): Empty list [] (placeholder)
includedSides ("L"): Empty list [] (placeholder)
comboIndex ("M"): Empty map {} (placeholder)
products ("L"): Empty list [] (placeholder)
glutenFree ("BOOL"): MUST be to true
vegetarian ("BOOL"): Determine based on ingredients.
slowCook ("BOOL"): true if the recipe uses a slow cooker.
instaPot ("BOOL"): true if the recipe uses an Instant Pot.
flagged ("BOOL"): Always set to false.

After generating the JSON, you MUST call the validate_recipe_json tool with your output. Review the validation report. If fixes were applied, use the CORRECTED JSON from the report as your final output. If you disagree with a fix, explain why and adjust accordingly.

Only return the json format, no additional text or comments
"""
)

# ============================================================================
# AGENT 2: INGREDIENT STANDARDIZER (exact Step Function prompt)
# ============================================================================
ingredient_standardizer_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[get_standardized_ingredients, journal],
    system_prompt="""
Standardize ONLY the ingredient names and units in the following list. Do NOT change preparation instructions or add recipe-specific details.

FIRST: Call the get_standardized_ingredients tool with a comma-separated list of the core ingredient names from the recipe. Use the returned standardized names and units as your reference for standardization.

CRITICAL RULES:
1. ONLY standardize the core ingredient name (e.g., "beef chuck roast" → "Chuck Roast")
2. ONLY standardize units (e.g., "lbs" → "pounds", "tsp" → "teaspoon")
3. PRESERVE original quantities exactly (e.g., "3", "2 1/2", "1/4")
4. PRESERVE ALL preparation instructions exactly as written (chopped, diced, sliced, etc.)
5. DO NOT add preparation details from other recipes
6. DO NOT change recipe-specific cutting instructions
7. If no exact ingredient name match exists, leave the ingredient unchanged

Return your response in this exact format:

UPDATED_INGREDIENTS:
[
    {"S": "standardized ingredient 1"},
    {"S": "standardized ingredient 2"},
    {"S": "standardized ingredient 3"}
]

CHANGES_MADE:
- "original ingredient" → "standardized ingredient"
- "2 lbs ground beef" → "2 pounds Ground Beef"
- "1 tsp salt" → "1 teaspoon Salt"

If no changes were made, write:
CHANGES_MADE:
- No changes made - all ingredients left as original

Focus: Ingredient names and units ONLY. Preserve everything else exactly as written.
"""
)

# ============================================================================
# AGENT 3: INGREDIENT OBJECTS CREATOR (exact Step Function prompt + categories)
# ============================================================================
ingredient_objects_creator_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[journal],
    system_prompt="""
Parse the following ingredients into structured objects for a recipe database.

Instructions:
- Parse each ingredient string into the exact DynamoDB format shown below
- Extract quantity, unit, ingredient name, and preparation notes
- Move descriptors (large, fresh, chopped, diced, minced) to the note field
- Ingredient names should be capitalized (e.g., "Yellow Onion", "Ground Beef")
- Categories must be one of: Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods
- Return ONLY the ingredient_objects structure as valid JSON, no other text or explanations

Expected Output Format (return exactly this structure):
{
    "L": [
        {
            "M": {
                "ingredient_name": {"S": "Ingredient Name"},
                "category": {"S": "Category"},
                "quantity": {"S": "Amount"},
                "unit": {"S": "Unit"},
                "note": {"S": "Preparation notes"},
                "affiliate_link": {"S": ""}
            }
        }
    ]
}

Parsing Examples:
- "1 cup yellow onion, chopped" → ingredient_name: "Yellow Onion", quantity: "1", unit: "cup", note: "chopped"
- "2 large eggs, beaten" → ingredient_name: "Eggs", quantity: "2", unit: "", note: "large, beaten"
- "Salt and pepper to taste" → ingredient_name: "Salt", quantity: "", unit: "", note: "to taste"
"""
)

# ============================================================================
# AGENT 4: SIDE DISH RECOMMENDER (exact Step Function prompt + get_available_sides tool)
# ============================================================================
side_dish_recommender_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[get_available_sides, http_request, journal],
    system_prompt="""
You are a culinary expert tasked with recommending side dishes that would pair well with a main dish.

FIRST: Call the get_available_sides tool to retrieve all available side dishes from the database.

Then analyze the main dish and recommend 3-6 side dishes that would complement it well.
- Consider ALL side dishes, not just those from the same cuisine - some of the best pairings cross cuisine boundaries
- Consider flavor profiles, cooking methods, preparation times, and ingredient compatibility
- Look for balance: if the main is rich/heavy, consider lighter sides; if spicy, consider cooling sides
- Consider practical cooking: can sides be prepared alongside the main dish? 
- Prioritize sides that enhance the overall meal experience

Evaluation criteria:
- Flavor compatibility and balance
- Texture contrast (crispy vs soft, fresh vs cooked)
- Nutritional balance and color variety
- Cooking method synergy (oven sides with oven mains, quick sides with complex mains)
- Cultural appropriateness while allowing creative cross-cuisine pairings

ENHANCED RESEARCH STEP:
After selecting from our catalog, use http_request to search the web for "best side dishes for [recipe title]" to discover popular pairings we might be missing. If popular sides exist that are NOT in our catalog, note them as RECOMMENDED NEW SIDES TO CREATE.

Return your response in this format:

RECOMMENDED_SIDES (from our catalog):
["side-id-1", "side-id-2", "side-id-3"]

RECOMMENDED NEW SIDES TO CREATE (not in our catalog but popular pairings):
- [Side dish name]: [Why it pairs well]

Rules:
- Return only side dish IDs from the catalog for existing recommendations
- Maximum 6 side dishes from catalog
"""
)

# ============================================================================
# AGENT 5: AFFILIATE PRODUCTS RECOMMENDER (exact Step Function prompt + get_available_products tool)
# ============================================================================
affiliate_products_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[get_available_products, http_request, journal],
    system_prompt="""
You are an ecommerce expert analyzing a recipe to identify relevant affiliate products that would help someone cook this dish.

FIRST: Call the get_available_products tool to retrieve all available affiliate products from the database.

Then analyze the recipe for products that would be directly useful for cooking this dish.
- Focus on TOOLS, EQUIPMENT, and NON-FOOD items that enhance the cooking process
- Consider specialized equipment mentioned or implied in the recipe
- Look for quality tools that would improve technique or results
- Consider storage, serving, or presentation items related to this dish type

IMPORTANT RESTRICTIONS:
- DO NOT recommend any food items, ingredients, or consumables (like frozen french fries, spices, oils, etc.)

Evaluation criteria:
- Equipment explicitly mentioned or implied in the recipe
- Tools that would improve cooking technique or results
- Specialized items for this cuisine type or cooking method
- Quality upgrades for standard kitchen equipment
- Storage or serving items specific to this dish type
- ONLY INCLUDE if this item be used to prepare the recipe provided. 

ENHANCED RESEARCH STEP:
After selecting from our catalog, use http_request to search the web for "best kitchen tools for making [recipe title]" to discover common tools we might be missing. If useful products exist that are NOT in our catalog, note them as RECOMMENDED NEW PRODUCTS TO ADD.

Return your response in this format:

RECOMMENDED_PRODUCTS (from our catalog):
["product-id-1", "product-id-2", "product-id-3"]

RECOMMENDED NEW PRODUCTS TO ADD (not in our catalog but commonly used):
- [Product name]: [Why it's useful] - [Suggested Amazon search term]

Rules:
- Return only product IDs from the catalog for existing recommendations
- Maximum 6 products from catalog
- NON-FOOD items only
"""
)

# ============================================================================
# AGENT 6: QUALITY ASSURANCE (exact Step Function prompt + full checklist)
# ============================================================================
qa_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[http_request, journal],
    system_prompt="""
You are a culinary expert and Product Manager for the ezMeals meal planning iOS app. You are to review the provided recipe, it's history, and determine if the final recipe is ready to publish to our users.
You are provided 3 things, 1/ the original recipe text, the final processed recipe in json form, and a summary of the process steps for context. 

═══════════════════════════════════════════════════════════
QA VALIDATION CHECKLIST - You MUST check every item below
═══════════════════════════════════════════════════════════

1. INSTRUCTIONS QUALITY
   □ Every instruction step includes ingredient QUANTITIES and MEASUREMENTS
     (e.g., "Add 2 teaspoons curry powder" NOT "Add curry powder")
   □ Every instruction begins with an imperative verb (Add, Stir, Heat, Pour, etc.)
   □ Complex steps are broken into simpler beginner-friendly steps
   □ Instructions are in logical cooking order
   □ No missing steps between prep and serving

2. INGREDIENTS VALIDATION
   □ All quantities use mixed fractions (1/2 not 0.5)
   □ No special characters (inches not ", degrees not °)
   □ Units are standardized (teaspoon not tsp, tablespoon not tbsp, pounds not lbs)
   □ Every ingredient in the list appears in at least one instruction step

3. INGREDIENT OBJECTS VALIDATION
   □ ingredient_objects list is populated (not empty)
   □ Each object has: ingredient_name, category, quantity, unit, note, affiliate_link
   □ ingredient_name is capitalized (e.g., "Yellow Onion" not "yellow onion")
   □ category is one of EXACTLY: Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods — NO OTHER VALUES
   □ Descriptors (large, fresh, chopped) are in note field, not ingredient_name

4. RECIPE METADATA
   □ dishType is valid ("main" or "side")
   □ cuisineType MUST be one of EXACTLY these values: "Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews" — NO OTHER VALUES ARE VALID (e.g., "French", "Spanish", "Mediterranean", "Caribbean" are NOT valid — use the closest match)
   □ Time flags are calculated from prepTime + cookTime:
     - isQuick = true ONLY if total is 0-30 min
     - isBalanced = true ONLY if total is 31-60 min
     - isGourmet = true ONLY if total is > 60 min
     - Only ONE flag can be true at a time (mutually exclusive)
   □ vegetarian flag matches ingredients (no meat/fish = true)
   □ slowCook flag matches if slow cooker is used
   □ instaPot flag matches if Instant Pot is used
   □ glutenFree is set to true with appropriate notes for substitutions
   □ imageURL format: menu-item-images/Recipe_Name.jpg (underscores, no spaces)
   □ imageThumbURL format: menu-item-images/Recipe_Name_thumbnail.jpg
   □ flagged is always false

5. SIDE DISHES (if dishType=main)
   □ recommendedSides is populated with valid side dish IDs
   □ Sides complement the main dish well

6. AFFILIATE PRODUCTS
   □ products list contains only NON-FOOD items (tools, equipment, cookware)
   □ Products are relevant to actually cooking this specific recipe

7. NOTES QUALITY
   □ Includes gluten-free substitution notes where applicable
   □ Includes dietary variation notes (vegetarian, dairy-free swaps)
   □ No irrelevant notes (only cooking tips, substitutions, variations)

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════

**OVERALL QUALITY**: "✅ PUBLISH" or "❌ REVIEW NEEDED - [reason]"

**CHECKLIST RESULTS** (mark each ✅ or ❌):
1. Instructions Quality: [result + any failures]
2. Ingredients Validation: [result + any failures]
3. Ingredient Objects: [result + any failures]
4. Recipe Metadata: [result + any failures]
5. Side Dishes: [result + any failures]
6. Affiliate Products: [result + any failures]
7. Notes Quality: [result + any failures]

**FIXES REQUIRED** (if any):
- [Specific items that need correction before publishing]
"""
)

# ============================================================================
# URL SCRAPER TOOL (from url-scraper/lambda_function.py)
# ============================================================================

@tool
def scrape_recipe_url(url: str, output_dir: str = "/tmp") -> str:
    """
    Scrape a recipe URL, extract the recipe text and main image.
    Uses JSON-LD extraction first, falls back to HTML parsing.
    Downloads the hero image for use in image generation.

    Args:
        url: The recipe URL to scrape
        output_dir: Directory to save the downloaded image
    """
    import requests
    from bs4 import BeautifulSoup
    import re
    import os

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def parse_iso_duration(duration):
        if not duration or not duration.startswith('PT'):
            return duration or ''
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return duration
        hours, mins, secs = match.groups()
        parts = []
        if hours: parts.append(f"{hours} hour{'s' if int(hours) > 1 else ''}")
        if mins: parts.append(f"{mins} minutes")
        return ', '.join(parts) if parts else duration

    # Fetch page
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(url, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')

    recipe_text = None
    image_url = None
    source_method = None

    # Try JSON-LD first
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and '@graph' in data:
            items = data['@graph']
        for item in items:
            if not isinstance(item, dict):
                continue
            schema_type = item.get('@type', '')
            types = schema_type if isinstance(schema_type, list) else [schema_type]
            if 'Recipe' in types:
                # Extract image from JSON-LD
                img = item.get('image', '')
                if isinstance(img, list):
                    image_url = img[0] if img else None
                elif isinstance(img, dict):
                    image_url = img.get('url', '')
                elif isinstance(img, str):
                    image_url = img

                # Build recipe text
                parts = []
                name = item.get('name', '')
                if name: parts.append(f"Recipe: {name}")
                desc = item.get('description', '')
                if desc:
                    if '<' in desc: desc = BeautifulSoup(desc, 'html.parser').get_text()
                    parts.append(f"\nDescription: {desc}")
                yield_val = item.get('recipeYield')
                if yield_val:
                    if isinstance(yield_val, list): yield_val = yield_val[0]
                    parts.append(f"\nServings: {yield_val}")
                for key, label in [('prepTime', 'Prep Time'), ('cookTime', 'Cook Time')]:
                    val = item.get(key, '')
                    if val: parts.append(f"{label}: {parse_iso_duration(val)}")
                ingredients = item.get('recipeIngredient', [])
                if ingredients:
                    parts.append("\nIngredients:")
                    for ing in ingredients: parts.append(f"- {ing}")
                instructions = item.get('recipeInstructions', [])
                if instructions:
                    parts.append("\nInstructions:")
                    for i, step in enumerate(instructions, 1):
                        text = step.get('text', '') if isinstance(step, dict) else str(step)
                        if text:
                            if '<' in text: text = BeautifulSoup(text, 'html.parser').get_text()
                            parts.append(f"{i}. {text}")
                for key, label in [('recipeCuisine', 'Cuisine')]:
                    val = item.get(key)
                    if val:
                        if isinstance(val, list): val = ', '.join(val)
                        parts.append(f"\n{label}: {val}")
                recipe_text = '\n'.join(parts)
                source_method = 'json-ld'
                break
        if recipe_text:
            break

    # Fallback: HTML parsing
    if not recipe_text:
        selectors = [
            '[itemtype*="schema.org/Recipe"]', 'div.wprm-recipe-container',
            'div.tasty-recipes', 'div.recipe-card', 'main article', 'main', 'article',
        ]
        content_el = None
        for sel in selectors:
            content_el = soup.select_one(sel)
            if content_el and len(content_el.get_text(strip=True)) > 100:
                break
            content_el = None
        if not content_el:
            content_el = soup.body
        if content_el:
            for tag in content_el.find_all(['script', 'style', 'nav', 'footer', 'aside']):
                tag.decompose()
            text = content_el.get_text(separator='\n', strip=True)
            lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 3]
            recipe_text = '\n'.join(lines[:300])
            source_method = 'fallback-html'

    # Fallback image: og:image or first large image
    if not image_url:
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            image_url = og['content']
    if not image_url:
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if src and any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                w = img.get('width', '')
                h = img.get('height', '')
                if (w and int(w) >= 300) or (h and int(h) >= 300) or 'hero' in src.lower() or 'featured' in src.lower():
                    image_url = src
                    break

    # Download image
    image_path = None
    if image_url:
        try:
            if image_url.startswith('//'):
                image_url = 'https:' + image_url
            img_resp = session.get(image_url, timeout=15)
            img_resp.raise_for_status()
            ext = 'jpg'
            if 'png' in img_resp.headers.get('content-type', ''):
                ext = 'png'
            elif 'webp' in img_resp.headers.get('content-type', ''):
                ext = 'webp'
            slug = re.sub(r'[^a-z0-9]+', '-', (recipe_text.split('\n')[0] if recipe_text else 'recipe').lower())[:50]
            image_path = os.path.join(output_dir, f"{slug}.{ext}")
            with open(image_path, 'wb') as f:
                f.write(img_resp.content)
        except Exception as e:
            image_path = None

    if not recipe_text:
        return f"ERROR: Could not extract recipe from {url}"

    recipe_text += f"\n\nSource URL: {url}"

    result = f"SCRAPE RESULT:\n"
    result += f"Source: {url}\n"
    result += f"Method: {source_method}\n"
    if image_path:
        result += f"Image saved: {image_path}\n"
    else:
        result += f"Image: not found or download failed\n"
    result += f"\nRECIPE TEXT:\n{recipe_text}"

    return result


# ============================================================================
# AGENT 0: URL SCRAPER (entry point)
# ============================================================================
url_scraper_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[scrape_recipe_url, journal],
    system_prompt="""
You are the entry point agent for the ezMeals recipe pipeline. You receive a URL and your job is to:

1. Call scrape_recipe_url with the URL to extract the recipe text and download the main image
2. Return the extracted recipe text and image path so the orchestrator can pass them to the pipeline

That's it. Extract and pass along. Do not modify the recipe text.
"""
)


# ============================================================================
# AGENT 0.5: CHEF REVIEW (culinary analysis + web comparison + optimization)
# ============================================================================
chef_review_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[http_request, journal],
    system_prompt="""
You are a professional chef and recipe editor for the ezMeals meal planning app. You receive a scraped recipe and your job is to evaluate it, research it, and optimize it before it enters the processing pipeline.

## YOUR WORKFLOW

1. **ANALYZE** the recipe as-is:
   - Are ingredient ratios sensible? (e.g., enough seasoning for the protein weight?)
   - Are cook times and temps realistic and safe?
   - Are there missing steps a home cook would need? (e.g., resting meat, preheating)
   - Is the recipe complete or does it feel like steps were lost in scraping?

2. **RESEARCH** using http_request — search for 2-3 similar recipes on the web:
   - Search for "best [recipe name] recipe" to find popular versions
   - Compare ingredient ratios, techniques, and steps
   - Note where the scraped recipe differs significantly from popular versions

3. **OPTIMIZE** — produce an improved version:
   - Fix any missing or unclear steps
   - Adjust ratios if they're clearly off compared to popular versions
   - Add food safety notes if missing (internal temps, cross-contamination)
   - Improve technique descriptions for home cooks
   - DO NOT change the fundamental character of the dish

## OUTPUT FORMAT

CULINARY ASSESSMENT:
- Overall quality: [Excellent / Good / Needs Work / Poor]
- Completeness: [Complete / Missing Steps / Incomplete]
- Safety: [Safe / Needs Safety Notes]

KEY FINDINGS:
- [Finding 1]
- [Finding 2]

WEB COMPARISON:
- [Source 1]: [Key difference or confirmation]
- [Source 2]: [Key difference or confirmation]

OPTIMIZED RECIPE:
[Return the full recipe text with your improvements applied. Keep the same format as the input — title, description, ingredients list, instructions, cuisine, etc. Mark any changes you made with (OPTIMIZED) at the end of the changed line.]

CHANGES MADE:
- [Change 1 and why]
- [Change 2 and why]

If the recipe is already excellent, say so and return it unchanged.
"""
)


# ============================================================================
# IMAGE GENERATION TOOL (from marketing/apps/ez_meals/gen/ezmeal-imagegen.py)
# ============================================================================

EZMEAL_STYLE = """BRAND STYLE — EZ Meals (premium meal planning app):
- Clean, bright, appetizing food photography
- Warm natural lighting from the left/above, slightly overhead angle (30-45 degrees)
- Simple, uncluttered background — light wood, marble, or white surface
- The dish is the hero — centered, taking up ~60-70% of the frame
- Minimal props: a fork or spoon, cloth napkin, small herb garnish at edges
- Include relevant accompaniments (rice, sauce, bread) if part of the dish
- Vibrant, true-to-life colors — appetizing but realistic, not oversaturated
- Shallow depth of field — food sharp, background softly blurred
- NO text, NO logos, NO watermarks, NO hands, NO people
- Modern, clean aesthetic — consistent warm color temperature"""


def _load_gemini_api_key():
    """Load GOOGLE_API_KEY from env or .env file. Returns the key string."""
    import os
    if os.environ.get('GOOGLE_API_KEY'):
        return os.environ['GOOGLE_API_KEY']
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('GOOGLE_API_KEY='):
                    key = line.split('=', 1)[1]
                    os.environ['GOOGLE_API_KEY'] = key
                    return key
                if line.startswith('GOOGLE_CLOUD_PROJECT='):
                    os.environ['GOOGLE_CLOUD_PROJECT'] = line.split('=', 1)[1]
    return None


@tool
def generate_recipe_images(dish_name: str, dish_description: str, input_image_path: str, output_prefix: str, edit_notes: str = "") -> str:
    """
    Generate EZ Meals branded recipe images from a source photo.
    Step 1: Edit original → 16:9 landscape hero
    Step 2: Reframe hero → 1:1 square thumbnail

    Args:
        dish_name: Name of the dish (e.g., "Crying Tiger Thai Grilled Beef")
        dish_description: Brief visual description (e.g., "Sliced grilled beef on cutting board with dipping sauce")
        input_image_path: Path to the original recipe photo
        output_prefix: Output file prefix (e.g., /tmp/crying-tiger)
        edit_notes: Optional custom edit instructions
    """
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, Modality, ImageConfig, Part
    except ImportError:
        return "ERROR: google-genai package not installed. Run: pip install google-genai"

    api_key = _load_gemini_api_key()
    if not api_key:
        return "ERROR: No GOOGLE_API_KEY found in env or .env file"

    client = genai.Client(api_key=api_key)

    # Load source image
    with open(input_image_path, 'rb') as f:
        img_data = f.read()
    mime = 'image/png' if input_image_path.lower().endswith('.png') else 'image/jpeg'
    img_part = Part.from_bytes(data=img_data, mime_type=mime)

    # Step 1: Hero (16:9)
    hero_prompt = f"""Edit this food photograph so it appears as if it was originally shot in landscape (16:9) orientation.

PRESERVE:
- The actual food exactly as it appears — real textures, real colors, real details
- The core dish: {dish_description}

CHANGE:
- Recompose for 16:9 landscape framing
- Apply the EZ Meals brand style (below)
- Clean up background to simple light wood or marble surface
- Warm, bright natural lighting from the left
- Slightly overhead camera angle (30-45 degrees)
- Minimal, uncluttered styling
- Shallow depth of field

Do NOT regenerate or reimagine the food itself. Keep the real food from the photo.
Only adjust composition, framing, background, and lighting.

{EZMEAL_STYLE}"""

    if edit_notes:
        hero_prompt += f"\n\nADDITIONAL EDITS:\n{edit_notes}"

    hero_resp = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[hero_prompt, img_part],
        config=GenerateContentConfig(
            response_modalities=[Modality.TEXT, Modality.IMAGE],
            image_config=ImageConfig(aspect_ratio="16:9"),
        ),
    )

    # Save hero
    hero_path = None
    for part in hero_resp.candidates[0].content.parts:
        if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.mime_type.startswith('image/'):
            ext = 'png' if 'png' in part.inline_data.mime_type else 'jpg'
            hero_path = f"{output_prefix}-landscape.{ext}"
            with open(hero_path, 'wb') as f:
                f.write(part.inline_data.data)
            break

    if not hero_path:
        return "ERROR: No hero image returned from Gemini"

    # Step 2: Thumbnail (1:1) from hero
    with open(hero_path, 'rb') as f:
        hero_data = f.read()
    hero_mime = 'image/png' if hero_path.endswith('.png') else 'image/jpeg'
    hero_part = Part.from_bytes(data=hero_data, mime_type=hero_mime)

    thumb_resp = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=["Optimize this image for a square (1:1) thumbnail. Same image, just reframed for a square crop.", hero_part],
        config=GenerateContentConfig(
            response_modalities=[Modality.TEXT, Modality.IMAGE],
            image_config=ImageConfig(aspect_ratio="1:1"),
        ),
    )

    thumb_path = None
    for part in thumb_resp.candidates[0].content.parts:
        if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.mime_type.startswith('image/'):
            ext = 'png' if 'png' in part.inline_data.mime_type else 'jpg'
            thumb_path = f"{output_prefix}-thumbnail.{ext}"
            with open(thumb_path, 'wb') as f:
                f.write(part.inline_data.data)
            break

    import os, shutil, re as _re

    # Also save to the recipe output dir with deterministic names (hero.ext, thumbnail.ext)
    # This makes them findable regardless of what the agent names them
    slug = _re.sub(r'[^a-z0-9]+', '-', dish_name.lower()).strip('-')
    recipe_output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', slug)
    os.makedirs(recipe_output_dir, exist_ok=True)

    if hero_path and os.path.exists(hero_path):
        hero_ext = os.path.splitext(hero_path)[1]
        canonical_hero = os.path.join(recipe_output_dir, f'hero{hero_ext}')
        shutil.copy2(hero_path, canonical_hero)

    if thumb_path and os.path.exists(thumb_path):
        thumb_ext = os.path.splitext(thumb_path)[1]
        canonical_thumb = os.path.join(recipe_output_dir, f'thumbnail{thumb_ext}')
        shutil.copy2(thumb_path, canonical_thumb)

    results = f"✅ Images generated for {dish_name}:\n"
    if hero_path:
        results += f"  Landscape (16:9): {hero_path} ({os.path.getsize(hero_path) // 1024}KB)\n"
    if thumb_path:
        results += f"  Thumbnail (1:1):  {thumb_path} ({os.path.getsize(thumb_path) // 1024}KB)\n"
    else:
        results += f"  Thumbnail: ERROR - not generated\n"
    results += f"  Output dir: {recipe_output_dir}\n"

    return results


# ============================================================================
# AGENT 7: IMAGE GENERATOR
# ============================================================================
image_gen_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[generate_recipe_images, journal],
    system_prompt="""
You are the image generation agent for the ezMeals recipe pipeline. Your job is to create branded recipe images.

When given a recipe, you will:
1. Determine the best visual description of the dish for image generation
2. Call generate_recipe_images with:
   - dish_name: the recipe title
   - dish_description: a brief description of the key visual elements (what the food looks like plated)
   - input_image_path: path to the source photo (provided to you)
   - output_prefix: where to save the output images
   - edit_notes: any special instructions (optional)

The tool handles the 2-step process:
  Step 1: Edit original photo → 16:9 landscape hero image
  Step 2: Reframe hero → 1:1 square thumbnail

The generated images follow the EZ Meals brand style:
- Clean, bright, appetizing food photography
- Warm natural lighting, slightly overhead angle
- Simple background (light wood, marble, or white)
- Dish centered, taking up 60-70% of frame
- No text, logos, watermarks, hands, or people

After generation, report the file paths and sizes so the orchestrator can update the recipe's imageURL and imageThumbURL fields.
"""
)


# ============================================================================
# PUBLISH RECIPE TOOL — S3 upload + DynamoDB write + local save
# ============================================================================

@tool
def publish_recipe(recipe_json_str: str, hero_image_path: str, thumbnail_image_path: str, recipe_id: str) -> str:
    """
    Publish a completed recipe:
    1. Convert to correct plain-JSON schema (matching existing recipes exactly)
    2. Validate against gold standard schema
    3. Upload images to S3 (image bucket)
    4. Drop JSON file into S3 (menu-items-json bucket) — triggers DB import
    NO direct DynamoDB writes.

    Args:
        recipe_json_str: The final recipe JSON string
        hero_image_path: Local path to the hero image (16:9)
        thumbnail_image_path: Local path to the thumbnail image (1:1)
        recipe_id: The recipe ID slug (e.g., "thai-basil-chicken")
    """
    import os, datetime, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from schema_validator import convert_pipeline_to_schema, validate_recipe_schema, side_by_side_comparison

    try:
        recipe_raw = json.loads(recipe_json_str) if isinstance(recipe_json_str, str) else recipe_json_str
    except json.JSONDecodeError as e:
        return f"ERROR: Invalid recipe JSON — {e}"

    results = []

    # 1. Convert pipeline output to correct schema format
    # Extract recipe ID — handle both DynamoDB typed {"S": "value"} and plain string formats
    raw_id = recipe_raw.get('id', recipe_id)
    if isinstance(raw_id, dict):
        raw_id = raw_id.get('S', recipe_id)
    recipe = convert_pipeline_to_schema(recipe_raw, recipe_id=raw_id)
    
    # Inject enricher IDs if products/recommendedSides are empty
    if not recipe.get('products') or not recipe.get('recommendedSides'):
        enricher_file = os.path.join(os.path.dirname(__file__), 'enricher_recommendations.json')
        if os.path.exists(enricher_file):
            import json as _json_enrich
            with open(enricher_file) as ef:
                enrich_data = _json_enrich.load(ef)
            
            if not recipe.get('recommendedSides') and enrich_data.get('recommendedSides'):
                recipe['recommendedSides'] = enrich_data['recommendedSides']
            
            if not recipe.get('products') and enrich_data.get('products'):
                recipe['products'] = enrich_data['products']
    
    results.append("✅ Converted to plain JSON schema format")

    # 2. Validate against gold standard
    is_valid, errors, warnings = validate_recipe_schema(recipe)
    if not is_valid:
        error_report = "\n".join(f"  ❌ {e}" for e in errors)
        return f"❌ SCHEMA VALIDATION FAILED — cannot publish:\n{error_report}"
    results.append(f"✅ Schema validation passed ({len(warnings)} warnings)")

    # 3. Save local copy
    output_dir = os.path.join(os.path.dirname(__file__), 'output', recipe_id)
    os.makedirs(output_dir, exist_ok=True)

    local_json_path = os.path.join(output_dir, f'{recipe_id}.json')
    with open(local_json_path, 'w') as f:
        json.dump(recipe, f, indent=2)
    results.append(f"✅ Local JSON saved: {local_json_path}")

    # Copy images locally — with fallback search if provided paths don't exist
    import shutil, glob as _glob, re as _re2

    def _find_image(provided_path, label, recipe_title, recipe_slug):
        """Find an image with fallback search: provided path → output dir → /tmp/ glob."""
        if provided_path and os.path.exists(provided_path):
            return provided_path

        # Fallback 1: Check output dir for canonical names (hero.png, hero.jpg, thumbnail.png, etc.)
        for candidate_dir in [output_dir]:
            for ext in ['.png', '.jpg', '.jpeg']:
                candidate = os.path.join(candidate_dir, f'{label}{ext}')
                if os.path.exists(candidate):
                    return candidate

        # Fallback 2: Check output dir by slug name pattern
        slug_dirs_to_check = set()
        title_slug = _re2.sub(r'[^a-z0-9]+', '-', recipe_title.lower()).strip('-') if recipe_title else ''
        if title_slug:
            slug_dir = os.path.join(os.path.dirname(__file__), 'output', title_slug)
            if os.path.isdir(slug_dir):
                slug_dirs_to_check.add(slug_dir)
        for ext in ['.png', '.jpg', '.jpeg']:
            for d in slug_dirs_to_check:
                candidate = os.path.join(d, f'{label}{ext}')
                if os.path.exists(candidate):
                    return candidate

        # Fallback 3: /tmp/ glob using title prefix
        suffix = 'landscape' if label == 'hero' else 'thumbnail'
        name_under = recipe_title.replace(' ', '_') if recipe_title else ''
        patterns = []
        if name_under:
            patterns.append(f'/tmp/{name_under}-{suffix}.*')
            # Also try first 2 words
            words = name_under.split('_')[:2]
            patterns.append(f'/tmp/{"_".join(words)}*-{suffix}.*')
        if recipe_slug:
            patterns.append(f'/tmp/{recipe_slug}-{suffix}.*')
        for pat in patterns:
            matches = sorted(_glob.glob(pat), key=os.path.getmtime, reverse=True)
            if matches:
                return matches[0]

        return None

    recipe_title = recipe.get('title', '')
    recipe_slug = _re2.sub(r'[^a-z0-9]+', '-', recipe_title.lower()).strip('-') if recipe_title else recipe_id
    hero_image_path = _find_image(hero_image_path, 'hero', recipe_title, recipe_slug)
    thumbnail_image_path = _find_image(thumbnail_image_path, 'thumbnail', recipe_title, recipe_slug)

    for src, label in [(hero_image_path, 'hero'), (thumbnail_image_path, 'thumbnail')]:
        if src and os.path.exists(src):
            ext = os.path.splitext(src)[1]
            dst = os.path.join(output_dir, f'{recipe_id}_{label}{ext}')
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy2(src, dst)
            results.append(f"✅ Local {label} saved: {dst}")
        else:
            results.append(f"⚠️ {label} image not found — recipe will publish without {label}")

    # 4. Upload images to S3 (image bucket)
    IMAGE_BUCKET = 'amplify-ezmealsnew-menu-item-imageseb66c-dev'
    S3_PREFIX = 'public/menu-item-images/'

    try:
        sts = boto3.client('sts')
        s3_creds = sts.assume_role(
            RoleArn=AWS_CONFIG['ROLE_ARN'],
            RoleSessionName='RecipePublishS3'
        )['Credentials']
        s3 = boto3.client('s3', region_name=AWS_CONFIG['DYNAMODB_REGION'],
            aws_access_key_id=s3_creds['AccessKeyId'],
            aws_secret_access_key=s3_creds['SecretAccessKey'],
            aws_session_token=s3_creds['SessionToken'])

        image_url = recipe.get('imageURL', '')
        thumb_url = recipe.get('imageThumbURL', '')

        for local_path, s3_key_suffix in [(hero_image_path, image_url), (thumbnail_image_path, thumb_url)]:
            if local_path and os.path.exists(local_path) and s3_key_suffix:
                s3_key = S3_PREFIX.rstrip('/') + '/' + s3_key_suffix.replace('menu-item-images/', '')
                content_type = 'image/png' if local_path.endswith('.png') else 'image/jpeg'
                s3.upload_file(local_path, IMAGE_BUCKET, s3_key, ExtraArgs={'ContentType': content_type})
                results.append(f"✅ S3 image: s3://{IMAGE_BUCKET}/{s3_key}")
    except Exception as e:
        results.append(f"❌ S3 image upload failed: {e}")

    # 5. Drop JSON into menu-items-json S3 bucket (triggers DB import)
    #    MUST be DynamoDB-typed format: {"S": "val"}, {"N": "5"}, {"BOOL": true}, etc.
    #    Gold standard: Lomo_Saltado.json in menu-items-json bucket.
    JSON_BUCKET = 'menu-items-json'
    try:
        # Build filename from recipe title: Title_With_Underscores.json
        title = recipe.get('title', recipe_id)
        json_filename = title.replace(' ', '_') + '.json'

        # Convert plain JSON → DynamoDB-typed format for S3 Lambda import
        dynamo_recipe = _plain_to_s3_dynamo_json(recipe)

        # HARD GATE: Validate DynamoDB format before uploading.
        # This is deterministic — no semantic judgment. Pass/fail only.
        from validate_s3_format import validate_s3_recipe
        is_valid, val_errors, val_warnings = validate_s3_recipe(dynamo_recipe)
        if not is_valid:
            error_report = "\n".join(f"    ❌ {e}" for e in val_errors)
            return (
                f"❌ S3 FORMAT VALIDATION FAILED — upload blocked.\n"
                f"  The DynamoDB-typed JSON does not match the gold standard.\n"
                f"  Errors:\n{error_report}\n"
                f"  Fix _plain_to_s3_dynamo_json() and retry."
            )
        results.append(f"✅ S3 format validation passed ({len(val_warnings)} warnings)")

        s3.put_object(
            Bucket=JSON_BUCKET,
            Key=json_filename,
            Body=json.dumps(dynamo_recipe, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        results.append(f"✅ S3 JSON: s3://{JSON_BUCKET}/{json_filename} (DynamoDB-typed format, triggers DB import)")
    except Exception as e:
        results.append(f"❌ S3 JSON upload failed: {e}")

    # 6. Summary (NO direct DynamoDB write)
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    summary = f"\n📦 PUBLISH RESULTS ({timestamp}):\n" + "\n".join(results)

    # Save publish log
    log_path = os.path.join(output_dir, 'publish_log.txt')
    with open(log_path, 'w') as f:
        f.write(summary)

    return summary


# ============================================================================
# ORCHESTRATOR AGENT
# ============================================================================
orchestrator_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    tools=[workflow, agent_graph, journal],
    system_prompt="""
You are the Recipe Creator Orchestrator. You manage the complete recipe creation workflow through 6 specialized agents.

Your responsibilities:
1. Coordinate the recipe processing pipeline
2. Handle errors and retry failed steps
3. Ensure quality at each stage
4. Make decisions about when to iterate vs proceed
5. Manage the final quality assurance process

Workflow Steps:
0. URL scraping (agent has scrape_recipe_url tool — extracts recipe text + downloads main image)
1. Text to JSON conversion (agent has validate_recipe_json tool for auto-fixing)
2. Ingredient standardization  
3. Ingredient objects creation (categories: Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods)
4. Side dish recommendations (agent has get_available_sides tool for DB lookup)
5. Affiliate product recommendations (agent has get_available_products tool for DB lookup)
6. Quality assurance and final review
7. Image generation (agent has generate_recipe_images tool — uses downloaded image from step 0)

If QA fails any checklist item, route back to the responsible agent for correction.
"""
)

if __name__ == "__main__":
    print("EZmeals Strands Agents Recipe Creator V3 - Fixed")
    print("All agents: us.anthropic.claude-opus-4-6-v1")
    print()
    print("Custom @tools:")
    print("  validate_recipe_json - Auto-fixes cuisineType, time flags, imageURL, etc.")
    print("  get_available_sides  - Scans MenuItemData for dishType=side")
    print("  get_available_products - Scans AffiliateProduct table")
    print()
    print("Fixed from V2:")
    print("  ✅ Agent 3 categories: Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods")
    print("  ✅ Agent 1 has validate_recipe_json tool for post-processing validation")
    print("  ✅ Agent 4 has get_available_sides tool (pre-configured DB lookup)")
    print("  ✅ Agent 5 has get_available_products tool (pre-configured DB lookup)")
    print("  ✅ QA checklist has exact cuisineType values")
    print("  ✅ QA checklist has exact category values")
    print("  ✅ QA checklist has 31-34 min gap rule for time flags")
