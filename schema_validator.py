"""
EZ Meals Recipe Schema Validator
Compares pipeline output against gold standard recipe format.
Ensures EXACT schema match before publishing to S3.
"""
import json, os, re
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GOLD_STANDARD_PATH = os.path.join(SCRIPT_DIR, 'GOLD_STANDARD_RECIPE.json')

# The EXACT 36 keys every recipe must have (no more, no less)
REQUIRED_KEYS = {
    'baseMainId', 'comboIndex', 'cookTime', 'createdAt', 'cuisineType',
    'description', 'dishType', 'dressing', 'flagged', 'glutenFree',
    'id', 'imageThumbURL', 'imageURL', 'includedSides', 'ingredient_objects',
    'ingredients', 'instaPot', 'instructions', 'isBalanced', 'isGourmet',
    'isQuick', 'link', 'notes', 'optionalToppings', 'prepTime', 'primary',
    'products', 'rating', 'recommendedSides', 'sauce', 'searchTerms',
    'seasonings', 'slowCook', 'title', 'updatedAt', 'vegetarian'
}

# Expected types for each field
FIELD_TYPES = {
    'baseMainId': str,
    'comboIndex': dict,         # Plain dict (empty {} for non-combos)
    'cookTime': str,           # String number e.g. "10"
    'createdAt': str,          # ISO timestamp
    'cuisineType': str,
    'description': str,
    'dishType': str,
    'dressing': list,          # list[str]
    'flagged': bool,
    'glutenFree': bool,
    'id': str,                 # UUID
    'imageThumbURL': str,
    'imageURL': str,
    'includedSides': list,     # list[str] (IDs)
    'ingredient_objects': str, # JSON-stringified list of objects
    'ingredients': list,       # list[str]
    'instaPot': bool,
    'instructions': list,      # list[str]
    'isBalanced': bool,
    'isGourmet': bool,
    'isQuick': bool,
    'link': str,
    'notes': list,             # list[str]
    'optionalToppings': list,  # list[str]
    'prepTime': str,           # String number e.g. "5"
    'primary': bool,
    'products': list,          # list[str] (IDs)
    'rating': str,             # String number e.g. "0"
    'recommendedSides': list,  # list[str] (IDs)
    'sauce': list,             # list[str]
    'searchTerms': list,       # list[str]
    'seasonings': list,        # list[str]
    'slowCook': bool,
    'title': str,
    'updatedAt': str,          # ISO timestamp
    'vegetarian': bool,
}

VALID_CUISINES = ["Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"]
VALID_DISH_TYPES = ["main", "side"]
VALID_CATEGORIES = {"Produce", "Proteins", "Dairy", "Grains & Bakery", "Pantry Staples", "Seasonings", "Frozen Foods"}


def validate_recipe_schema(recipe: dict) -> tuple[bool, list[str], list[str]]:
    """
    Validate a recipe dict against the gold standard schema.
    Returns: (is_valid, errors, warnings)
    """
    errors = []
    warnings = []
    
    recipe_keys = set(recipe.keys())
    
    # 1. Check for missing keys
    missing = REQUIRED_KEYS - recipe_keys
    if missing:
        errors.append(f"MISSING KEYS: {sorted(missing)}")
    
    # 2. Check for extra keys
    extra = recipe_keys - REQUIRED_KEYS
    if extra:
        errors.append(f"EXTRA KEYS (not in schema): {sorted(extra)}")
    
    # 3. Check types
    for key, expected_type in FIELD_TYPES.items():
        if key not in recipe:
            continue
        val = recipe[key]
        if not isinstance(val, expected_type):
            errors.append(f"WRONG TYPE: {key} should be {expected_type.__name__}, got {type(val).__name__} = {repr(val)[:100]}")
    
    # 4. Check list contents are strings (not dicts/objects)
    list_of_strings_fields = [
        'dressing', 'ingredients', 'instructions', 'notes', 
        'optionalToppings', 'products', 'recommendedSides', 
        'sauce', 'searchTerms', 'seasonings', 'includedSides'
    ]
    for field in list_of_strings_fields:
        val = recipe.get(field, [])
        if isinstance(val, list):
            for i, item in enumerate(val):
                if not isinstance(item, str):
                    errors.append(f"LIST ITEM NOT STRING: {field}[{i}] is {type(item).__name__} = {repr(item)[:100]}")
    
    # 5. Check ingredient_objects is stringified JSON (not a list/dict)
    io = recipe.get('ingredient_objects')
    if isinstance(io, str):
        try:
            parsed = json.loads(io)
            if not isinstance(parsed, list):
                errors.append(f"ingredient_objects: parsed to {type(parsed).__name__}, expected list")
            else:
                # Validate each object has required fields
                for i, obj in enumerate(parsed):
                    required_io_keys = {'ingredient_name', 'category', 'quantity', 'unit'}
                    obj_keys = set(obj.keys())
                    missing_io = required_io_keys - obj_keys
                    if missing_io:
                        errors.append(f"ingredient_objects[{i}] missing keys: {missing_io}")
                    cat = obj.get('category', '')
                    if cat and cat not in VALID_CATEGORIES:
                        errors.append(f"ingredient_objects[{i}] invalid category: '{cat}'")
        except json.JSONDecodeError as e:
            errors.append(f"ingredient_objects: not valid JSON string: {e}")
    elif isinstance(io, (list, dict)):
        errors.append(f"ingredient_objects: must be a JSON STRING, not {type(io).__name__}")
    
    # 6. Check comboIndex is a dict (empty {} for non-combos, populated for combos)
    ci = recipe.get('comboIndex', {})
    if not isinstance(ci, dict):
        errors.append(f"comboIndex: must be a dict, got {type(ci).__name__}: {repr(ci)[:100]}")
    
    # 7. Validate specific field values
    if recipe.get('cuisineType') not in VALID_CUISINES:
        errors.append(f"Invalid cuisineType: '{recipe.get('cuisineType')}'")
    
    if recipe.get('dishType') not in VALID_DISH_TYPES:
        errors.append(f"Invalid dishType: '{recipe.get('dishType')}'")
    
    # Time flags must be mutually exclusive
    quick = recipe.get('isQuick', False)
    balanced = recipe.get('isBalanced', False)
    gourmet = recipe.get('isGourmet', False)
    time_flags_true = sum([quick, balanced, gourmet])
    if time_flags_true != 1:
        errors.append(f"Time flags: exactly 1 must be true, got {time_flags_true} (Q={quick}, B={balanced}, G={gourmet})")
    
    # Verify time flags match actual times
    try:
        total = int(recipe.get('prepTime', '0')) + int(recipe.get('cookTime', '0'))
        expected_quick = total <= 30
        expected_balanced = 31 <= total <= 60
        expected_gourmet = total > 60
        if quick != expected_quick or balanced != expected_balanced or gourmet != expected_gourmet:
            errors.append(f"Time flags don't match times: {total}min should be Q={expected_quick} B={expected_balanced} G={expected_gourmet}, got Q={quick} B={balanced} G={gourmet}")
    except ValueError:
        errors.append(f"prepTime/cookTime not valid numbers: {recipe.get('prepTime')}/{recipe.get('cookTime')}")
    
    # Image URLs
    img = recipe.get('imageURL', '')
    if img and not img.startswith('menu-item-images/'):
        errors.append(f"imageURL must start with 'menu-item-images/', got: {img}")
    thumb = recipe.get('imageThumbURL', '')
    if thumb and not thumb.startswith('menu-item-images/'):
        errors.append(f"imageThumbURL must start with 'menu-item-images/', got: {thumb}")
    
    # Products and recommendedSides should be UUID-like strings
    for field in ['products', 'recommendedSides']:
        val = recipe.get(field, [])
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and len(item) > 0:
                    # Should look like a UUID or ID, not an object description
                    if '{' in item or 'name' in item.lower():
                        errors.append(f"{field}: items should be ID strings, got object-like: {repr(item)[:80]}")
    
    # Rating should be "0" (string)
    if recipe.get('rating') not in ['0', '5']:
        warnings.append(f"rating is '{recipe.get('rating')}' — existing recipes use '0'")
    
    # flagged must be false
    if recipe.get('flagged') is not False:
        errors.append(f"flagged must be false, got {recipe.get('flagged')}")
    
    # Timestamps
    for ts_field in ['createdAt', 'updatedAt']:
        ts = recipe.get(ts_field, '')
        if isinstance(ts, str) and ts:
            try:
                datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except ValueError:
                errors.append(f"{ts_field}: not valid ISO timestamp: {ts}")
    
    is_valid = len(errors) == 0
    return is_valid, errors, warnings


def side_by_side_comparison(recipe: dict, gold_path: str = GOLD_STANDARD_PATH) -> str:
    """
    Generate a side-by-side comparison of recipe vs gold standard.
    Returns a formatted report string.
    """
    with open(gold_path) as f:
        gold = json.load(f)
    
    report = []
    report.append("=" * 70)
    report.append("SCHEMA COMPARISON: New Recipe vs Gold Standard")
    report.append("=" * 70)
    report.append(f"New: {recipe.get('title', '?')}")
    report.append(f"Gold: {gold.get('title', '?')}")
    report.append("")
    
    all_keys = sorted(REQUIRED_KEYS)
    
    for key in all_keys:
        new_val = recipe.get(key)
        gold_val = gold.get(key)
        
        new_type = type(new_val).__name__ if new_val is not None else 'MISSING'
        gold_type = type(gold_val).__name__ if gold_val is not None else 'MISSING'
        
        match = '✅' if new_type == gold_type else '❌'
        
        if key not in recipe:
            match = '❌'
            report.append(f"{match} {key}: MISSING (gold={gold_type})")
        elif new_type != gold_type:
            report.append(f"{match} {key}: type={new_type} (gold={gold_type})")
            if isinstance(new_val, (dict, list)):
                report.append(f"     NEW: {repr(new_val)[:120]}")
            report.append(f"     GOLD: {repr(gold_val)[:120]}")
        else:
            # Same type - brief preview
            if isinstance(new_val, str):
                preview = new_val[:60] + '...' if len(new_val) > 60 else new_val
                report.append(f"{match} {key}: {repr(preview)}")
            elif isinstance(new_val, bool):
                report.append(f"{match} {key}: {new_val}")
            elif isinstance(new_val, list):
                report.append(f"{match} {key}: list[{len(new_val)} items]")
            else:
                report.append(f"{match} {key}: {repr(new_val)[:80]}")
    
    return '\n'.join(report)


def convert_pipeline_to_schema(pipeline_json: dict, recipe_id: str = None) -> dict:
    """
    Convert pipeline output (DynamoDB-typed or partially correct) to the exact schema format.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    
    def extract_val(val):
        """Extract value from DynamoDB format or return as-is."""
        if isinstance(val, dict):
            if 'S' in val: return val['S']
            if 'N' in val: return val['N']
            if 'BOOL' in val: return val['BOOL']
            if 'L' in val: return [extract_val(v) for v in val['L']]
            if 'M' in val: return {k: extract_val(v) for k, v in val['M'].items()}
            if 'NULL' in val: return None
        return val
    
    # Unwrap DynamoDB format if present
    unwrapped = {}
    for k, v in pipeline_json.items():
        unwrapped[k] = extract_val(v)
    
    r = unwrapped
    
    # Build the correct schema
    recipe = {
        'id': recipe_id or r.get('id', ''),
        'title': r.get('title', ''),
        'dishType': r.get('dishType', 'main'),
        'primary': r.get('dishType', 'main') == 'main',  # MUST match dishType
        'baseMainId': r.get('baseMainId', ''),
        'imageURL': r.get('imageURL', ''),
        'imageThumbURL': r.get('imageThumbURL', ''),
        'description': r.get('description', ''),
        'link': r.get('link', ''),
        'prepTime': str(r.get('prepTime', '0')),
        'cookTime': str(r.get('cookTime', '0')),
        'rating': '5',  # Default "5" — matches existing pipeline recipes
        'cuisineType': r.get('cuisineType', 'Global Cuisines'),
        'isQuick': bool(r.get('isQuick', False)),
        'isBalanced': bool(r.get('isBalanced', False)),
        'isGourmet': bool(r.get('isGourmet', False)),
        'glutenFree': bool(r.get('glutenFree', True)),
        'vegetarian': bool(r.get('vegetarian', False)),
        'slowCook': bool(r.get('slowCook', False)),
        'instaPot': bool(r.get('instaPot', False)),
        'flagged': False,
        'createdAt': now,
        'updatedAt': now,
        
        # List[str] fields
        'ingredients': _ensure_string_list(r.get('ingredients', [])),
        'instructions': _ensure_string_list(r.get('instructions', [])),
        'notes': _ensure_string_list(r.get('notes', [])),
        'searchTerms': _ensure_string_list(r.get('searchTerms', [])),
        'recommendedSides': _ensure_id_list(r.get('recommendedSides', [])),
        'includedSides': _ensure_id_list(r.get('includedSides', [])),
        'products': _ensure_id_list(r.get('products', [])),
        'dressing': _ensure_string_list(r.get('dressing', [])),
        'sauce': _ensure_string_list(r.get('sauce', [])),
        'seasonings': _ensure_string_list(r.get('seasonings', [])),
        'optionalToppings': _ensure_string_list(r.get('optionalToppings', [])),
        
        # Stringified JSON fields
        'ingredient_objects': _ensure_stringified_json(r.get('ingredient_objects', [])),
        'comboIndex': _ensure_dict(r.get('comboIndex', {})),
    }
    
    return recipe


def _ensure_string_list(val) -> list:
    """Ensure val is a list of plain strings."""
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except:
            return [val]
    if not isinstance(val, list):
        return []
    result = []
    for item in val:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            # DynamoDB format {"S": "value"} or object with title/name
            if 'S' in item:
                result.append(item['S'])
            elif 'title' in item:
                result.append(item['title'].get('S', item['title']) if isinstance(item['title'], dict) else str(item['title']))
            elif 'name' in item:
                result.append(item['name'].get('S', item['name']) if isinstance(item['name'], dict) else str(item['name']))
            else:
                result.append(json.dumps(item))
        else:
            result.append(str(item))
    return result


def _ensure_id_list(val) -> list:
    """Ensure val is a list of ID strings (not objects)."""
    if not isinstance(val, list):
        return []
    result = []
    for item in val:
        if isinstance(item, str):
            # Only include if it looks like an ID (UUID-ish)
            if len(item) > 10 and '-' in item:
                result.append(item)
        elif isinstance(item, dict):
            # Extract ID if present
            id_val = item.get('id') or item.get('S')
            if id_val:
                result.append(id_val)
    return result


def _ensure_dict(val) -> dict:
    """Ensure val is a plain dict. Unwraps DynamoDB M format and parses JSON strings."""
    if isinstance(val, dict):
        if 'M' in val:
            return val['M']
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except:
            pass
        return {}
    return {}


def _ensure_stringified_json(val) -> str:
    """Ensure val is a JSON string."""
    if isinstance(val, str):
        # Already a string — validate it's valid JSON
        try:
            json.loads(val)
            return val
        except:
            return '[]' if '[' in val else '{}'
    elif isinstance(val, (list, dict)):
        # Need to stringify
        # For ingredient_objects: convert from DynamoDB format if needed
        if isinstance(val, list):
            clean = []
            for item in val:
                if isinstance(item, dict):
                    obj = {}
                    for k, v in item.items():
                        if k == 'M':
                            # DynamoDB Map — unwrap
                            for mk, mv in v.items():
                                obj[mk] = mv.get('S', mv.get('N', mv.get('BOOL', ''))) if isinstance(mv, dict) else mv
                        elif isinstance(v, dict) and 'S' in v:
                            obj[k] = v['S']
                        elif isinstance(v, dict) and 'N' in v:
                            obj[k] = v['N']
                        else:
                            obj[k] = v
                    clean.append(obj)
                else:
                    clean.append(item)
            return json.dumps(clean)
        return json.dumps(val)
    return '[]'


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            recipe = json.load(f)
        is_valid, errors, warnings = validate_recipe_schema(recipe)
        print(side_by_side_comparison(recipe))
        print()
        if errors:
            print(f"❌ VALIDATION FAILED — {len(errors)} errors:")
            for e in errors:
                print(f"  ❌ {e}")
        if warnings:
            print(f"⚠️  {len(warnings)} warnings:")
            for w in warnings:
                print(f"  ⚠️  {w}")
        if is_valid:
            print("✅ SCHEMA VALID — ready to publish to S3")
    else:
        print("Usage: python schema_validator.py <recipe.json>")
