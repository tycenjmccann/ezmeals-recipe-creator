#!/usr/bin/env python3
"""
Post-pipeline verification checklist for EZ Meals recipes.
Rex runs this BEFORE telling Tycen anything is done.
Every published recipe must pass ALL checks.
"""

import json
import os
import sys
import re

sys.path.insert(0, '/tmp/pip-install')

VALID_CUISINES = ["Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"]
VALID_CATEGORIES = ["Produce", "Proteins", "Dairy", "Grains & Bakery", "Pantry Staples", "Seasonings", "Frozen Foods"]
VALID_DISH_TYPES = ["main", "side"]
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def extract_val(v):
    """Extract plain value from DynamoDB format or plain format."""
    if isinstance(v, dict):
        if 'S' in v: return v['S']
        if 'N' in v: return v['N']
        if 'BOOL' in v: return v['BOOL']
        if 'L' in v: return [extract_val(i) for i in v['L']]
        if 'M' in v: return {k: extract_val(vv) for k, vv in v['M'].items()}
        return v
    if isinstance(v, list):
        return [extract_val(i) for i in v]
    return v


def normalize_recipe(recipe: dict) -> dict:
    """Convert a recipe from DynamoDB format to plain format if needed."""
    # Detect DynamoDB format: any top-level value is a dict with S/N/BOOL/L/M key
    is_dynamo = any(
        isinstance(v, dict) and bool({'S', 'N', 'BOOL', 'L', 'M'} & set(v.keys()))
        for v in recipe.values() if isinstance(v, dict)
    )
    if is_dynamo:
        return {k: extract_val(v) for k, v in recipe.items()}
    return recipe


def verify_recipe(recipe_raw: dict, recipe_name: str = "") -> tuple:
    """
    Run the full verification checklist on a recipe.
    Returns (passed: bool, results: list[str])
    Each result is "✅ ..." or "❌ ..."
    """
    recipe = normalize_recipe(recipe_raw)
    results = []
    failures = 0

    def check(condition, msg):
        nonlocal failures
        if condition:
            results.append(f"  ✅ {msg}")
        else:
            results.append(f"  ❌ {msg}")
            failures += 1

    results.append(f"\n{'='*60}")
    results.append(f"VERIFICATION: {recipe_name or recipe.get('title', 'Unknown')}")
    results.append(f"{'='*60}")

    # 1. REQUIRED FIELDS (36 keys)
    results.append("\n1. SCHEMA COMPLETENESS")
    expected_keys = {
        'id', 'title', 'dishType', 'primary', 'baseMainId', 'imageURL', 'imageThumbURL',
        'description', 'link', 'prepTime', 'cookTime', 'rating', 'cuisineType',
        'isQuick', 'isBalanced', 'isGourmet', 'ingredients', 'ingredient_objects',
        'instructions', 'notes', 'recommendedSides', 'includedSides', 'comboIndex',
        'products', 'searchTerms', 'glutenFree', 'vegetarian', 'slowCook', 'instaPot',
        'flagged', 'sauce', 'seasonings', 'dressing', 'optionalToppings',
        'createdAt', 'updatedAt'
    }
    actual_keys = set(recipe.keys())
    missing = expected_keys - actual_keys
    extra = actual_keys - expected_keys
    check(len(actual_keys) == 36, f"Has 36 keys (actual: {len(actual_keys)})")
    if missing:
        results.append(f"    Missing: {missing}")
    if extra:
        results.append(f"    Extra: {extra}")

    # 2. FIELD TYPES
    results.append("\n2. FIELD TYPES")
    check(isinstance(recipe.get('id'), str) and len(recipe.get('id', '')) > 10, "id is a UUID string")
    check(isinstance(recipe.get('title'), str) and len(recipe.get('title', '')) > 0, "title is non-empty string")
    check(recipe.get('dishType') in VALID_DISH_TYPES, f"dishType is valid ({recipe.get('dishType')})")
    check(isinstance(recipe.get('primary'), bool), "primary is boolean")
    check(isinstance(recipe.get('ingredients'), list) and len(recipe.get('ingredients', [])) > 0, f"ingredients is non-empty list ({len(recipe.get('ingredients', []))} items)")
    check(isinstance(recipe.get('instructions'), list) and len(recipe.get('instructions', [])) > 0, f"instructions is non-empty list ({len(recipe.get('instructions', []))} items)")
    check(isinstance(recipe.get('ingredient_objects'), str), "ingredient_objects is a JSON string (not list/dict)")
    check(isinstance(recipe.get('flagged'), bool) and recipe.get('flagged') == False, "flagged is false")

    # 3. INGREDIENT OBJECTS
    results.append("\n3. INGREDIENT OBJECTS")
    io_str = recipe.get('ingredient_objects', '')
    io_parsed = None
    if isinstance(io_str, str) and io_str:
        try:
            io_parsed = json.loads(io_str)
        except:
            pass
    if isinstance(io_str, list):
        io_parsed = io_str  # Already a list

    if io_parsed and isinstance(io_parsed, list):
        check(len(io_parsed) > 0, f"ingredient_objects is populated ({len(io_parsed)} objects)")
        # Check each object
        bad_cats = []
        bad_names = []
        missing_fields = []
        for i, obj in enumerate(io_parsed):
            if isinstance(obj, dict):
                for field in ['ingredient_name', 'category', 'quantity', 'unit', 'note']:
                    if field not in obj:
                        missing_fields.append(f"Object {i}: missing {field}")
                cat = obj.get('category', '')
                if cat and cat not in VALID_CATEGORIES:
                    bad_cats.append(f"{cat} (in {obj.get('ingredient_name', '?')})")
                name = obj.get('ingredient_name', '')
                if name and name[0].islower():
                    bad_names.append(name)
        check(len(missing_fields) == 0, f"All objects have required fields" + (f" — missing: {missing_fields[:3]}" if missing_fields else ""))
        check(len(bad_cats) == 0, f"All categories valid" + (f" — bad: {bad_cats[:3]}" if bad_cats else ""))
        check(len(bad_names) == 0, f"All ingredient names capitalized" + (f" — lowercase: {bad_names[:3]}" if bad_names else ""))
    else:
        check(False, "ingredient_objects is parseable and populated")

    # 4. METADATA
    results.append("\n4. METADATA")
    check(recipe.get('cuisineType') in VALID_CUISINES, f"cuisineType valid ({recipe.get('cuisineType')})")
    
    def extract_num(val):
        if isinstance(val, dict):
            return int(val.get('N', 0) or 0)
        return int(val or 0)
    
    prep = extract_num(recipe.get('prepTime', 0))
    cook = extract_num(recipe.get('cookTime', 0))
    total = prep + cook
    is_quick = recipe.get('isQuick', False)
    is_balanced = recipe.get('isBalanced', False)
    is_gourmet = recipe.get('isGourmet', False)
    # Handle DynamoDB format {"BOOL": true}
    if isinstance(is_quick, dict): is_quick = is_quick.get('BOOL', False)
    if isinstance(is_balanced, dict): is_balanced = is_balanced.get('BOOL', False)
    if isinstance(is_gourmet, dict): is_gourmet = is_gourmet.get('BOOL', False)
    time_flags = [is_quick, is_balanced, is_gourmet]
    check(sum(time_flags) == 1, f"Exactly one time flag true (quick={is_quick}, balanced={is_balanced}, gourmet={is_gourmet})")
    
    if total <= 30:
        check(is_quick, f"isQuick=true for {total}min total")
    elif total <= 60:
        check(is_balanced, f"isBalanced=true for {total}min total")
    else:
        check(is_gourmet, f"isGourmet=true for {total}min total")

    img = recipe.get('imageURL', '')
    check(img.startswith('menu-item-images/') and img.endswith('.jpg'), f"imageURL format correct ({img})")
    thumb = recipe.get('imageThumbURL', '')
    check(thumb.startswith('menu-item-images/') and '_thumbnail.jpg' in thumb, f"imageThumbURL format correct ({thumb})")

    # 5. PRODUCTS (CRITICAL)
    results.append("\n5. PRODUCTS")
    products = recipe.get('products', [])
    check(isinstance(products, list) and len(products) > 0, f"products is non-empty ({len(products)} items)")
    if products:
        all_uuids = all(isinstance(p, str) and UUID_PATTERN.match(p) for p in products)
        check(all_uuids, f"All product values are UUID strings")

    # 6. RECOMMENDED SIDES (CRITICAL for mains)
    results.append("\n6. RECOMMENDED SIDES")
    sides = recipe.get('recommendedSides', [])
    is_main = recipe.get('dishType') == 'main'
    if is_main:
        check(isinstance(sides, list) and len(sides) > 0, f"recommendedSides is non-empty ({len(sides)} items)")
        if sides:
            all_uuids = all(isinstance(s, str) and UUID_PATTERN.match(s) for s in sides)
            check(all_uuids, f"All side values are UUID strings")
    else:
        results.append("  ⏭️  Skipped (dish is a side)")

    # 7. SEARCH TERMS
    results.append("\n7. SEARCH TERMS")
    terms = recipe.get('searchTerms', [])
    check(isinstance(terms, list) and len(terms) > 0, f"searchTerms is non-empty ({len(terms)} items)")
    if terms:
        check(all(isinstance(t, str) for t in terms), "All search terms are strings")

    # 8. NOTES
    results.append("\n8. NOTES")
    notes = recipe.get('notes', [])
    check(isinstance(notes, list), "notes is a list")
    if recipe.get('glutenFree') == True:
        gf_note = any('gluten' in str(n).lower() for n in notes)
        check(gf_note, "GF recipe has gluten-free substitution note")

    # 9. TIMESTAMPS
    results.append("\n9. TIMESTAMPS")
    check(isinstance(recipe.get('createdAt'), str) and len(recipe.get('createdAt', '')) > 0, "createdAt populated")
    check(isinstance(recipe.get('updatedAt'), str) and len(recipe.get('updatedAt', '')) > 0, "updatedAt populated")

    # SUMMARY
    results.append(f"\n{'='*60}")
    if failures == 0:
        results.append(f"✅ ALL CHECKS PASSED")
    else:
        results.append(f"❌ {failures} CHECK(S) FAILED")
    results.append(f"{'='*60}")

    return failures == 0, results


def verify_s3_recipe(recipe_key: str) -> tuple:
    """Fetch a recipe from S3 and verify it."""
    import boto3
    sts = boto3.client('sts')
    creds = sts.assume_role(
        RoleArn='arn:aws:iam::970547358447:role/CrossAccountDynamoDBWriter',
        RoleSessionName='verify'
    )['Credentials']
    
    s3 = boto3.client('s3',
        region_name='us-west-1',
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken']
    )
    
    resp = s3.get_object(Bucket='menu-items-json', Key=recipe_key)
    recipe = json.loads(resp['Body'].read())
    return verify_recipe(recipe, recipe_key)


def verify_all_recent(output_dir: str = None) -> dict:
    """Verify all recipes in the output directory."""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(__file__), 'output')
    
    all_results = {"passed": [], "failed": []}
    
    for recipe_dir in sorted(os.listdir(output_dir)):
        dir_path = os.path.join(output_dir, recipe_dir)
        if not os.path.isdir(dir_path):
            continue
        
        json_files = [f for f in os.listdir(dir_path) if f.endswith('.json')]
        if not json_files:
            continue
        
        with open(os.path.join(dir_path, json_files[0])) as f:
            recipe = json.load(f)
        
        passed, results = verify_recipe(recipe, recipe_dir)
        for r in results:
            print(r)
        
        if passed:
            all_results["passed"].append(recipe_dir)
        else:
            all_results["failed"].append(recipe_dir)
    
    print(f"\n{'='*60}")
    print(f"BATCH VERIFICATION SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Passed: {len(all_results['passed'])}")
    print(f"❌ Failed: {len(all_results['failed'])}")
    if all_results['failed']:
        print(f"Failed recipes: {all_results['failed']}")
    
    return all_results


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--s3':
        key = sys.argv[2] if len(sys.argv) > 2 else 'Chicken_Pad_Thai.json'
        passed, results = verify_s3_recipe(key)
        for r in results:
            print(r)
    elif len(sys.argv) > 1 and sys.argv[1] == '--all':
        verify_all_recent()
    else:
        verify_all_recent()
