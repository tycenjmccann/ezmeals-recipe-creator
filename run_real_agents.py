"""
REAL Strands Agents Pipeline Test
Actually runs recipes through the agent pipeline with Bedrock calls.
"""

from strands import Agent
import json
import uuid
import sys

# Step 1 Agent - Text to JSON with exact Step Function prompt
step1_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
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
isBalanced ("BOOL"): true if prepTime + cookTime is 35-60 min, else false
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

Only return the json format, no additional text or comments
"""
)

# 2 problem recipes from the DB that had missing quantities in instructions
recipes = [
    {
        "name": "Slow Cooker Coconut Curry Lentils",
        "text": """
Slow Cooker Coconut Curry Lentils

Ingredients:
- 1 large yellow onion, diced
- 4 garlic cloves, minced
- 1 cup brown lentils, rinsed
- 2 medium sweet potatoes, peeled and cubed
- 3 large carrots, sliced
- 2 teaspoons curry powder
- 1/4 teaspoon ground cloves
- 1 can (14.5 oz) diced tomatoes
- 1 can (8 oz) tomato sauce
- 3 cups vegetable broth
- 1 can (14 oz) coconut milk
- 2 cups cooked rice for serving
- 1/4 cup red onion, diced for garnish
- 1/4 cup fresh cilantro, chopped
- 2 green onions, sliced
- Salt to taste

Instructions:
1. Add the onion, garlic, sweet potato, carrots, lentils, curry powder, cloves, diced tomatoes, tomato sauce, and vegetable broth to slow cooker.
2. Cover and cook on low for 6-8 hours or high for 3-4 hours.
3. Stir in coconut milk during last 30 minutes of cooking.
4. Season with salt to taste.
5. Serve over rice and garnish with red onion, cilantro, and green onions.

Prep Time: 15 minutes
Cook Time: 360 minutes (6 hours)
Serves: 6
"""
    },
    {
        "name": "Jamaican Jerk Chicken",
        "text": """
Jamaican Jerk Chicken

Ingredients:
- 2 lbs chicken tenders
- 3 tablespoons Jamaican jerk seasoning
- 2 tablespoons olive oil
- 1 tablespoon soy sauce
- 2 tablespoons lime juice
- 1 tablespoon brown sugar
- 3 cloves garlic, minced
- 1 scotch bonnet pepper, minced
- 2 cups jasmine rice
- 1 can (14 oz) coconut milk
- 1 cup water
- Salt and pepper to taste

Instructions:
1. Season the chicken tenders all over with Jamaican jerk seasoning.
2. Heat oil in a skillet and cook chicken until done.
3. Make coconut rice by combining rice, coconut milk, and water in a pot.
4. Bring to a boil, reduce heat, cover and simmer for 18 minutes.
5. Serve chicken over coconut rice with lime wedges.

Prep Time: 10 minutes
Cook Time: 25 minutes
Serves: 4
"""
    }
]

print("🧪 REAL STRANDS AGENTS PIPELINE TEST")
print("=" * 70)
print("Testing if agents fix the quantity-in-instructions issue")
print("Using model: us.anthropic.claude-opus-4-6-v1")
print("=" * 70)

for i, recipe in enumerate(recipes, 1):
    recipe_id = str(uuid.uuid4())
    print(f"\n{'='*70}")
    print(f"📝 RECIPE {i}: {recipe['name']}")
    print(f"Recipe ID: {recipe_id}")
    print(f"{'='*70}")
    
    prompt = f"""
Recipe Details:
{recipe['text']}

Use recipe ID: {recipe_id}
"""
    
    print(f"\n🤖 Running Agent 1 (Text to JSON)...")
    sys.stdout.flush()
    
    result = step1_agent(prompt)
    
    print(f"\n✅ AGENT OUTPUT:")
    print(result.message)
    print(f"\n{'='*70}")
