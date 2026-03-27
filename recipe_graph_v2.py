"""
EZmeals Recipe Creator — Graph Pipeline v2 (Production)
10-node sequential graph mirroring Step Functions pipeline.

Usage:
    python recipe_graph_v2.py <recipe_url>
    python recipe_graph_v2.py https://www.recipetineats.com/chicken-pad-thai/

Nodes:
    scraper → chef_review → json_converter → ingredient_standardizer →
    ingredient_objects → enricher → search_terms → qa_review → image_gen → publish
"""
import sys, os, json, time, traceback, asyncio, uuid, re

# Allow running from any directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from strands import Agent
from strands.multiagent.graph import GraphBuilder
from strands_agents_recipe_creator import (
    scrape_recipe_url, validate_recipe_json,
    get_available_sides, get_available_products,
    get_standardized_ingredients, generate_recipe_images,
    publish_recipe,
    CUISINE_TYPES, VALID_CATEGORIES,
)

LOG_FILE = os.path.join(SCRIPT_DIR, 'pipeline.log')

def log(msg):
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

# ============================================================================
# PROMPTS — Ported from Step Functions Lambdas
# ============================================================================

RECIPE_ID = str(uuid.uuid4()).lower()

SCRAPER_PROMPT = """You scrape recipe URLs. Call scrape_recipe_url with the URL.
Return the COMPLETE recipe text VERBATIM — every single ingredient with exact quantities,
every instruction step word-for-word, prep time, cook time, servings, and the image path.
Do NOT summarize, paraphrase, or omit ANY detail. Return it exactly as extracted."""

CHEF_REVIEW_PROMPT = """You are a professional chef reviewing a scraped recipe before it enters our processing pipeline.

Your job is to IMPROVE the recipe while preserving its character. Check:
1. Are ingredient RATIOS sensible? (e.g., sauce-to-protein ratio, seasoning levels)
2. Are cooking TIMES realistic for the method described?
3. Are instructions COMPLETE? (no missing steps between major transitions)
4. Are there SAFETY notes needed? (e.g., raw chicken handling, hot oil)
5. Do ALL ingredients appear in the instructions with their QUANTITIES?
6. Are there common MISTAKES to warn about? (e.g., "tamarind puree NOT concentrate")

Output the IMPROVED recipe with:
- ALL original ingredients preserved with exact quantities
- ALL instructions preserved and enhanced where needed
- Any added safety notes or technique tips
- A brief "CHANGES MADE" section listing what you modified and why

Be concise. No essays. Just the improved recipe text."""

JSON_CONVERTER_PROMPT = f"""Convert detailed recipe information into a JSON file adhering to the specified schema and formatting compatible with DynamoDB's requirements. Ensure the output uses the specified fields:

1. Convert Recipe Details to DynamoDB-Compatible JSON Schema:
Use the correct data type indicators ("S", "N", "BOOL", "L", "M").

Required Fields:
id ("S"): Use this unique ID: "{RECIPE_ID}"
title ("S"): The exact name of the recipe.
dishType ("S"): "main" for main dishes, "side" for side dishes
primary ("BOOL"): true for main dishes, false for side dishes
baseMainId ("S"): Empty string "" (placeholder for combos)
imageURL ("S"): Construct the filename using menu-item-images/[recipe_name].[extension], replacing spaces with underscores (_) in [recipe_name]. Use .jpg extension.
imageThumbURL ("S"): Construct the filename using menu-item-images/[recipe_name]_thumbnail.[extension], replacing spaces with underscores (_) in [recipe_name]. Use .jpg extension.
description ("S"): A concise, engaging summary of the recipe that makes readers want to try it out, add some reference to the cuisine type if applicable.
link ("S"): Extract any URL from the recipe text, or empty string "" if none found
prepTime ("N"): Preparation time in minutes (as string number e.g. "20").
cookTime ("N"): Cooking time in minutes (as string number e.g. "10").
rating ("N"): "5"
servings ("S"): Number of servings (e.g., "4", "6-8")
cuisineType ("S"): Type of cuisine from this list: {json.dumps(CUISINE_TYPES)}
isQuick ("BOOL"): true if prepTime + cookTime is 0-30 min, else false
isBalanced ("BOOL"): true if prepTime + cookTime is 31-60 min, else false
isGourmet ("BOOL"): true if prepTime + cookTime is > 60 min, else false
NOTE: isQuick, isBalanced, isGourmet are MUTUALLY EXCLUSIVE. Exactly ONE must be true.
ingredients ("L"): A list of ingredient strings. Preserve original quantities and convert to mixed fractions if needed (e.g., "1/2" not "0.5"). Avoid special characters (use "inches" not "). Each item format: {{"S": "ingredient string"}}
ingredient_objects ("L"): Empty list [] (placeholder — will be populated in a later step)
instructions ("L"): Enhance Cooking Instructions: Begin each step with an imperative verb. Break complex steps into simpler steps easy for beginners to follow. Include ingredient quantities within the instructions for ease of use and clarity. Each item format: {{"S": "instruction string"}}
notes ("L"): Notes should be limited. Include gluten-free and/or other dietary substitutes like: use gluten-free noodles, flour, etc. Remove notes not directly related to making the recipe or substitutions/variations. Each item format: {{"S": "note string"}}
recommendedSides ("L"): Empty list [] (placeholder)
includedSides ("L"): Empty list [] (placeholder)
comboIndex ("M"): Empty map {{}} (placeholder)
products ("L"): Empty list [] (placeholder)
searchTerms ("L"): Empty list [] (placeholder — will be populated in a later step)
glutenFree ("BOOL"): Set to true if the recipe CAN be prepared gluten-free with reasonable substitutions. Most recipes qualify — just swap soy sauce for tamari, regular flour for GF flour, etc. If glutenFree is true, you MUST include a note explaining the GF substitutions (e.g., "For gluten-free: use tamari instead of soy sauce and gluten-free oyster sauce"). Only set to false if the recipe fundamentally cannot work without gluten (e.g., fresh pasta, bread-based dishes where gluten structure is essential).
vegetarian ("BOOL"): Determine based on ingredients.
slowCook ("BOOL"): true if the recipe uses a slow cooker, else false.
instaPot ("BOOL"): true if the recipe uses an Instant Pot, else false.
flagged ("BOOL"): Always set to false.

CRITICAL: Return ONLY the JSON. No markdown code blocks, no explanations, no text before or after. Just the raw JSON object.
After generating the JSON, call validate_recipe_json with the JSON string to auto-fix any issues. Return the corrected version."""

INGREDIENT_STANDARDIZER_PROMPT = """You standardize ingredient names and units in recipes.

Steps:
1. Call get_standardized_ingredients with a comma-separated list of the CORE ingredient names from the recipe (e.g., "chicken breast, brown sugar, fish sauce, rice noodles")
2. Use the returned standardized names as your reference
3. Update ONLY ingredient names and units in the recipe JSON

CRITICAL RULES:
1. ONLY standardize the core ingredient name (e.g., "beef chuck roast" → "Chuck Roast")
2. ONLY standardize units (e.g., "lbs" → "pounds", "tsp" → "teaspoon", "tbsp" → "tablespoon")
3. PRESERVE original quantities EXACTLY (e.g., "3", "2 1/2", "1/4")
4. PRESERVE ALL preparation instructions EXACTLY as written (chopped, diced, sliced, etc.)
5. DO NOT add preparation details from other recipes
6. DO NOT change recipe-specific cutting instructions
7. If no exact ingredient name match exists, leave the ingredient UNCHANGED

Return the updated ingredients list as a JSON array of {"S": "ingredient string"} objects.
Also return a CHANGES_MADE section showing each change: "original" → "standardized"."""

INGREDIENT_OBJECTS_PROMPT = """Parse the ingredients into structured objects for a recipe database.

Instructions:
- Parse each ingredient string into the exact DynamoDB format shown below
- Extract quantity, unit, ingredient name, and preparation notes
- Move descriptors (large, fresh, chopped, diced, minced) to the note field
- Ingredient names should be capitalized (e.g., "Yellow Onion", "Ground Beef")
- Categories MUST be one of: """ + ", ".join(sorted(VALID_CATEGORIES)) + """
- Return ONLY the ingredient_objects structure as valid JSON, no other text

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
- "1 1/2 tablespoons fish sauce" → ingredient_name: "Fish Sauce", quantity: "1 1/2", unit: "tablespoon", note: ""
- "150g chicken breast, thinly sliced" → ingredient_name: "Chicken Breast", quantity: "150", unit: "g", note: "thinly sliced" """

ENRICHER_PROMPT = """You enrich recipes with side dish and product recommendations.

Step 1 — SIDE DISHES:
Call get_available_sides to get our complete side dish catalog.
Analyze the recipe and recommend 3-6 sides that complement it well. Consider:
- Flavor profiles, cooking methods, preparation times, ingredient compatibility
- Balance: if main is rich/heavy, consider lighter sides; if spicy, consider cooling sides
- Cross-cuisine pairings are OK if they make culinary sense
- Practical cooking: can sides be prepared alongside the main?
Return side dish IDs as a JSON array.

Step 2 — AFFILIATE PRODUCTS:
Call get_available_products to get our complete product catalog.
Recommend 2-4 products. Focus ONLY on TOOLS, EQUIPMENT, and NON-FOOD items. Consider:
- Equipment explicitly mentioned or implied in the recipe
- Tools that improve cooking technique or results
- Specialized items for this cuisine type or cooking method
IMPORTANT: DO NOT recommend food items, ingredients, or consumables.
Return product IDs as a JSON array.

Return both arrays clearly labeled:
RECOMMENDED_SIDES: [...]
RECOMMENDED_PRODUCTS: [...]"""

SEARCH_TERMS_PROMPT = """You generate search terms for a recipe to help users find it even when they don't know the exact name.

Given the recipe data from previous pipeline steps, generate up to 4 SHORT search terms that:
- Help users find this recipe using words NOT already in the title, description, or ingredients
- Are NOT cuisine types (already a searchable attribute)
- Are NOT dietary flags like "gluten-free" or "vegetarian" (already searchable attributes)
- Are NOT generic words like "dinner", "easy", "quick", "family", "meal", "recipe", "delicious"
- Would apply to ≤15% of our catalog (very specific only)

FOCUS ON:
- Specific dish categories (pasta, soup, salad, stir-fry)
- Alternative dish names (pancakes → hotcakes, flapjacks)
- Specific cooking methods NOT already flagged (griddle, wok, dutch oven, plancha)
- Cultural/regional names (köttbullar for Swedish Meatballs, medianoche)
- Unique descriptors (crispy, creamy, smoky, tangy)

Return the search terms as a DynamoDB-formatted list:
{"L": [{"S": "term1"}, {"S": "term2"}, ...]}

If no useful terms exist beyond what's already searchable, return {"L": []}."""

QA_REVIEW_PROMPT = """You are a culinary expert and Product Manager for the ezMeals meal planning iOS app.
Review the provided recipe and determine if it is ready to publish to our users.

You will receive inputs from multiple pipeline nodes:
- **scraper**: The original recipe text (ground truth)
- **json_converter**: The structured DynamoDB JSON (the artifact to validate)
- **ingredient_objects**: The parsed ingredient objects
- **enricher**: Side dish and product recommendations
- **search_terms**: Generated search terms

Compare the ORIGINAL recipe from scraper against the FINAL JSON from json_converter.

Provide a CONCISE quality assessment:

**OVERALL QUALITY**: "High - Publish!" or "Review Needed - <reason>"

**INGREDIENT CHECK**:
- Does every instruction reference specific quantities? (not "add sauce" but "add 2 tbsp sauce")
- Are all ingredients in the ingredients list also in ingredient_objects?
- Do ingredient_objects have valid categories (Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods)?
- Were any ingredients from the ORIGINAL recipe lost or altered incorrectly?

**METADATA CHECK**:
- Is cuisineType valid? (American, Asian, Indian, Italian, Latin, Soups & Stews, Global Cuisines)
- Are time flags correct? (0-30min=isQuick, 31-60=isBalanced, >60=isGourmet, mutually exclusive)
- Is imageURL format correct? (menu-item-images/Recipe_Name.jpg)
- Is dishType "main" or "side"?

**SIDE DISHES**: Do the recommended sides complement the main well?

**PRODUCTS**: Would the recommended products be useful for this recipe? No food items?

**SEARCH TERMS**: Are the search terms useful and specific? Not duplicating title/cuisine/dietary flags?

**CULINARY IMPROVEMENTS**: Any suggestions to improve the recipe?

**GF/DIETARY**: Is glutenFree set correctly? Rule: if the recipe CAN be made GF with reasonable substitutions (tamari for soy sauce, GF flour, etc.), glutenFree should be TRUE with a note explaining the substitutions. Only false if gluten is structurally essential (bread, fresh pasta).

Keep it brief and practical. Lean towards publishing unless there are red flags.
If PASS, output the final assembled recipe JSON with recommendedSides, products, and searchTerms populated from the enricher's and search_terms node's recommendations."""

IMAGE_GEN_PROMPT = """You generate recipe images using the generate_recipe_images tool.

From the QA-approved recipe JSON, extract:
1. The dish name (title field)
2. A brief visual description for the image generator (from the description field)
3. The output prefix from the recipe name (e.g., "Black_Pepper_Chicken")

The scraper node downloads the original recipe photo to /tmp/. The filename follows the pattern:
  /tmp/recipe-{slug}.jpg
where {slug} is the URL slug (e.g., /tmp/recipe-black-pepper-chicken.jpg).

Call generate_recipe_images with:
- dish_name: the recipe title
- dish_description: a SHORT visual description focusing on what the dish LOOKS like (colors, textures, plating)
- input_image_path: the /tmp/recipe-*.jpg path from the scraper
- output_prefix: "/tmp/{Recipe_Name}" using underscores (e.g., "/tmp/Black_Pepper_Chicken")

Return the paths to the generated hero and thumbnail images."""

PUBLISH_PROMPT = """You publish completed recipes to the EZ Meals database.

From the previous pipeline steps, you have:
1. The final QA-approved recipe JSON (from qa_review)
2. The generated image paths (from image_gen) — typically:
   - Hero: /tmp/{Recipe_Name}-landscape.jpg (or .png)
   - Thumbnail: /tmp/{Recipe_Name}-thumbnail.jpg (or .png)

Call publish_recipe with:
- recipe_json_str: the COMPLETE final recipe JSON string from QA
- hero_image_path: the landscape image path from image_gen
- thumbnail_image_path: the thumbnail image path from image_gen
- recipe_id: the recipe's id field from the JSON

Return the publish result."""


# ============================================================================
# BUILD AND RUN GRAPH
# ============================================================================

def build_pipeline():
    """Build the 10-node recipe pipeline graph."""
    log("Creating agents...")

    scraper = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[scrape_recipe_url],
        system_prompt=SCRAPER_PROMPT,
        name="scraper",
    )
    chef = Agent(
        model="us.anthropic.claude-opus-4-6-v1",
        system_prompt=CHEF_REVIEW_PROMPT,
        name="chef_review",
    )
    converter = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[validate_recipe_json],
        system_prompt=JSON_CONVERTER_PROMPT,
        name="json_converter",
    )
    standardizer = Agent(
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tools=[get_standardized_ingredients],
        system_prompt=INGREDIENT_STANDARDIZER_PROMPT,
        name="ingredient_standardizer",
    )
    obj_creator = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        system_prompt=INGREDIENT_OBJECTS_PROMPT,
        name="ingredient_objects",
    )
    enricher = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[get_available_sides, get_available_products],
        system_prompt=ENRICHER_PROMPT,
        name="enricher",
    )
    search_terms = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        system_prompt=SEARCH_TERMS_PROMPT,
        name="search_terms",
    )
    qa = Agent(
        model="us.anthropic.claude-opus-4-6-v1",
        system_prompt=QA_REVIEW_PROMPT,
        name="qa_review",
    )
    image_gen = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[generate_recipe_images],
        system_prompt=IMAGE_GEN_PROMPT,
        name="image_gen",
    )
    publisher = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        tools=[publish_recipe],
        system_prompt=PUBLISH_PROMPT,
        name="publish",
    )

    log("Building graph...")
    builder = GraphBuilder()

    # Add all 10 nodes
    for name, agent in [
        ("scraper", scraper), ("chef_review", chef), ("json_converter", converter),
        ("ingredient_standardizer", standardizer), ("ingredient_objects", obj_creator),
        ("enricher", enricher), ("search_terms", search_terms), ("qa_review", qa),
        ("image_gen", image_gen), ("publish", publisher),
    ]:
        builder.add_node(agent, name)

    # Sequential pipeline
    builder.add_edge("scraper", "chef_review")
    builder.add_edge("chef_review", "json_converter")
    builder.add_edge("json_converter", "ingredient_standardizer")
    builder.add_edge("ingredient_standardizer", "ingredient_objects")
    builder.add_edge("ingredient_objects", "enricher")
    builder.add_edge("enricher", "search_terms")
    builder.add_edge("search_terms", "qa_review")
    builder.add_edge("qa_review", "image_gen")
    builder.add_edge("image_gen", "publish")

    # QA gets multi-edge input for full pipeline visibility
    builder.add_edge("scraper", "qa_review")
    builder.add_edge("json_converter", "qa_review")
    builder.add_edge("ingredient_objects", "qa_review")

    builder.set_entry_point("scraper")
    builder.set_execution_timeout(900)
    builder.set_node_timeout(300)

    return builder.build()


async def run_pipeline(url: str):
    """Run the full recipe pipeline on a URL."""
    open(LOG_FILE, 'w').close()
    log("=== STARTING PIPELINE ===")
    log(f"Recipe ID: {RECIPE_ID}")
    log(f"URL: {url}")

    graph = build_pipeline()
    log("Graph built — 10 nodes")

    start = time.time()
    result = await graph.invoke_async(f"Process this recipe URL: {url}")
    elapsed = time.time() - start

    log(f"\n✅ Pipeline complete in {elapsed:.0f}s ({elapsed/60:.1f}m)")
    log(f"Status: {result.status}")

    # Save each node's output
    for node_id in ['scraper', 'chef_review', 'json_converter', 'ingredient_standardizer',
                     'ingredient_objects', 'enricher', 'search_terms', 'qa_review',
                     'image_gen', 'publish']:
        node_result = result.results.get(node_id)
        if node_result and node_result.result:
            msg = node_result.result.message
            if msg and 'content' in msg:
                text = ''
                for block in msg['content']:
                    if 'text' in block:
                        text = block['text']
                        break
                log(f"\n{'='*60}")
                log(f"NODE: {node_id}")
                log(f"{'='*60}")
                log(text[:2000])

                output_path = os.path.join(SCRIPT_DIR, f'output_node_{node_id}.txt')
                with open(output_path, 'w') as f:
                    f.write(text)

    return result


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.recipetineats.com/chicken-pad-thai/"
    asyncio.run(run_pipeline(url))
