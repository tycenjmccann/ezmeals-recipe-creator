"""
Run all 21 recent recipes through the Strands Agents pipeline.
Captures before/after and agent notes for each recipe.
"""

from strands import Agent
import json
import uuid
import sys
import os
from datetime import datetime

# Agent 1: Text to JSON (exact Step Function prompt)
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

# QA Agent - validates the output
qa_agent = Agent(
    model="us.anthropic.claude-opus-4-6-v1",
    system_prompt="""
You are a QA reviewer for the ezMeals recipe app. Compare the ORIGINAL recipe (ingredients + instructions) against the AGENT-PROCESSED version and report what was improved or fixed.

Focus on these specific checks:
1. QUANTITIES IN INSTRUCTIONS: Does every instruction step now include ingredient quantities? Flag any step that says "add the onion" instead of "add 1 large diced yellow onion"
2. IMPERATIVE VERBS: Does every step start with an action verb?
3. BEGINNER FRIENDLY: Were complex steps broken into simpler ones?
4. INGREDIENT FORMAT: Are quantities in fractions (1/2 not 0.5)?
5. METADATA: Are time flags correct? cuisineType MUST be one of EXACTLY: "Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews" — no other values are valid.
6. NOTES: Are gluten-free substitutions included?

Return a CONCISE report:

FIXES APPLIED:
- [list each specific fix]

ISSUES REMAINING:
- [any problems still present]

QUALITY SCORE: [1-10]
"""
)

def extract_json_from_response(text):
    """Extract JSON from agent response that may contain markdown code blocks."""
    if '```json' in text:
        start = text.index('```json') + 7
        end = text.index('```', start)
        return text[start:end].strip()
    elif '```' in text:
        start = text.index('```') + 3
        end = text.index('```', start)
        return text[start:end].strip()
    return text.strip()

def run_pipeline():
    with open('recent_recipes_raw.json') as f:
        recipes = json.load(f)
    
    results = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print(f"🧪 STRANDS AGENTS PIPELINE - Processing {len(recipes)} recipes")
    print(f"Model: us.anthropic.claude-opus-4-6-v1")
    print("=" * 80)
    
    for i, recipe in enumerate(recipes, 1):
        recipe_id = str(uuid.uuid4())
        title = recipe['title']
        
        print(f"\n{'='*80}")
        print(f"📝 [{i}/{len(recipes)}] {title}")
        print(f"ID: {recipe_id}")
        print(f"{'='*80}")
        
        # Format the recipe as plain text
        ingredients_text = "\n".join([f"- {ing}" for ing in recipe['ingredients']])
        instructions_text = "\n".join([f"{j+1}. {step}" for j, step in enumerate(recipe['instructions'])])
        
        recipe_text = f"""
{title}

Ingredients:
{ingredients_text}

Instructions:
{instructions_text}
"""
        
        # BEFORE
        print(f"\n📋 BEFORE (from DB):")
        print(f"  Ingredients: {len(recipe['ingredients'])} items")
        print(f"  Instructions: {len(recipe['instructions'])} steps")
        for j, step in enumerate(recipe['instructions'][:3], 1):
            print(f"    {j}. {step[:100]}...")
        if len(recipe['instructions']) > 3:
            print(f"    ... +{len(recipe['instructions'])-3} more steps")
        
        # Run Agent 1
        print(f"\n🤖 Agent 1: Text to JSON...")
        sys.stdout.flush()
        
        try:
            agent1_result = step1_agent(f"Recipe Details:\n{recipe_text}\n\nUse recipe ID: {recipe_id}")
            agent1_text = agent1_result.message['content'][0]['text'] if isinstance(agent1_result.message, dict) else str(agent1_result.message)
            
            # Parse the JSON
            json_str = extract_json_from_response(agent1_text)
            processed = json.loads(json_str)
            
            # Extract processed instructions
            processed_instructions = [item['S'] for item in processed.get('instructions', {}).get('L', [])]
            processed_ingredients = [item['S'] for item in processed.get('ingredients', {}).get('L', [])]
            
            # AFTER
            print(f"\n✅ AFTER (Agent processed):")
            print(f"  Ingredients: {len(processed_ingredients)} items")
            print(f"  Instructions: {len(processed_instructions)} steps")
            for j, step in enumerate(processed_instructions[:3], 1):
                print(f"    {j}. {step[:100]}...")
            if len(processed_instructions) > 3:
                print(f"    ... +{len(processed_instructions)-3} more steps")
            
            # Run QA Agent
            print(f"\n🤖 Agent 6: QA Review...")
            sys.stdout.flush()
            
            qa_prompt = f"""
ORIGINAL RECIPE:
Title: {title}
Original Ingredients:
{ingredients_text}
Original Instructions:
{instructions_text}

AGENT-PROCESSED VERSION:
Processed Ingredients:
{chr(10).join([f'- {ing}' for ing in processed_ingredients])}
Processed Instructions:
{chr(10).join([f'{j+1}. {step}' for j, step in enumerate(processed_instructions)])}

Metadata:
- cuisineType: {processed.get('cuisineType', {}).get('S', 'N/A')}
- isQuick: {processed.get('isQuick', {}).get('BOOL', 'N/A')}
- isBalanced: {processed.get('isBalanced', {}).get('BOOL', 'N/A')}
- isGourmet: {processed.get('isGourmet', {}).get('BOOL', 'N/A')}
- vegetarian: {processed.get('vegetarian', {}).get('BOOL', 'N/A')}
- slowCook: {processed.get('slowCook', {}).get('BOOL', 'N/A')}
- glutenFree: {processed.get('glutenFree', {}).get('BOOL', 'N/A')}
"""
            
            qa_result = qa_agent(qa_prompt)
            qa_text = qa_result.message['content'][0]['text'] if isinstance(qa_result.message, dict) else str(qa_result.message)
            
            print(f"\n📊 QA REPORT:")
            print(qa_text)
            
            results.append({
                "recipe_number": i,
                "title": title,
                "recipe_id": recipe_id,
                "original_ingredients_count": len(recipe['ingredients']),
                "original_instructions_count": len(recipe['instructions']),
                "processed_ingredients_count": len(processed_ingredients),
                "processed_instructions_count": len(processed_instructions),
                "original_instructions": recipe['instructions'],
                "processed_instructions": processed_instructions,
                "qa_report": qa_text,
                "status": "success"
            })
            
        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            results.append({
                "recipe_number": i,
                "title": title,
                "recipe_id": recipe_id,
                "status": "error",
                "error": str(e)
            })
        
        # Save progress after each recipe
        with open(f'pipeline_results_{timestamp}.json', 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\n💾 Progress saved ({i}/{len(recipes)} complete)")
    
    # Final summary
    print(f"\n{'='*80}")
    print(f"🎉 PIPELINE COMPLETE")
    print(f"{'='*80}")
    
    successes = [r for r in results if r['status'] == 'success']
    errors = [r for r in results if r['status'] == 'error']
    
    print(f"✅ Successful: {len(successes)}/{len(recipes)}")
    print(f"❌ Errors: {len(errors)}/{len(recipes)}")
    
    if successes:
        avg_orig_steps = sum(r['original_instructions_count'] for r in successes) / len(successes)
        avg_new_steps = sum(r['processed_instructions_count'] for r in successes) / len(successes)
        print(f"\n📊 INSTRUCTION STATS:")
        print(f"  Avg original steps: {avg_orig_steps:.1f}")
        print(f"  Avg processed steps: {avg_new_steps:.1f}")
        print(f"  Avg steps added: {avg_new_steps - avg_orig_steps:.1f}")
    
    print(f"\n📁 Full results saved to: pipeline_results_{timestamp}.json")
    return results

if __name__ == "__main__":
    run_pipeline()
