#!/usr/bin/env python3
"""
Deterministic validator for EZ Meals S3 recipe JSON files.

Checks that a recipe JSON matches the EXACT DynamoDB-typed format 
that the Lambda import expects. No semantic judgment — pure structural 
comparison against the gold standard (Lomo_Saltado.json).

Usage:
    python validate_s3_format.py <recipe.json>
    python validate_s3_format.py --check-s3 <S3_Key.json>

Returns exit code 0 on pass, 1 on fail.
"""
import json
import sys
import os

# Expected DynamoDB type wrapper for each field.
# Derived from Lomo_Saltado.json (the gold standard).
# Format: field_name -> (wrapper_type, inner_type_for_lists)
EXPECTED_SCHEMA = {
    # String fields
    'id':             ('S', None),
    'title':          ('S', None),
    'dishType':       ('S', None),
    'baseMainId':     ('S', None),
    'imageURL':       ('S', None),
    'imageThumbURL':  ('S', None),
    'description':    ('S', None),
    'link':           ('S', None),
    'cuisineType':    ('S', None),
    'servings':       ('S', None),
    
    # Number fields
    'prepTime':       ('N', None),
    'cookTime':       ('N', None),
    'rating':         ('N', None),
    
    # Boolean fields
    'primary':        ('BOOL', None),
    'flagged':        ('BOOL', None),
    'glutenFree':     ('BOOL', None),
    'vegetarian':     ('BOOL', None),
    'slowCook':       ('BOOL', None),
    'instaPot':       ('BOOL', None),
    'isQuick':        ('BOOL', None),
    'isBalanced':     ('BOOL', None),
    'isGourmet':      ('BOOL', None),
    
    # Map fields
    'comboIndex':     ('M', None),
    
    # List of strings
    'ingredients':       ('L', 'S'),
    'instructions':      ('L', 'S'),
    'notes':             ('L', 'S'),
    'products':          ('L', 'S'),
    'recommendedSides':  ('L', 'S'),
    'includedSides':     ('L', 'S'),
    'searchTerms':       ('L', 'S'),
    'dressing':          ('L', 'S'),
    'sauce':             ('L', 'S'),
    'seasonings':        ('L', 'S'),
    'optionalToppings':  ('L', 'S'),
    
    # List of maps (ingredient objects)
    'ingredient_objects': ('L', 'M'),
}

# Required fields that MUST be present (from gold standard)
REQUIRED_FIELDS = {
    'id', 'title', 'dishType', 'primary', 'baseMainId',
    'imageURL', 'imageThumbURL', 'description', 'link',
    'prepTime', 'cookTime', 'rating', 'cuisineType',
    'isQuick', 'isBalanced', 'isGourmet',
    'glutenFree', 'vegetarian', 'slowCook', 'instaPot', 'flagged',
    'comboIndex',
    'ingredients', 'instructions', 'notes',
    'ingredient_objects',
    'products', 'recommendedSides', 'includedSides',
}

VALID_CUISINES = {"Global Cuisines", "American", "Asian", "Indian", "Italian", "Latin", "Soups & Stews"}
VALID_DISH_TYPES = {"main", "side"}


def validate_s3_recipe(recipe: dict) -> tuple[bool, list[str], list[str]]:
    """
    Validate a recipe JSON against the S3/Lambda DynamoDB-typed format.
    
    Returns: (is_valid, errors, warnings)
    """
    errors = []
    warnings = []
    
    # 1. Check required fields
    for field in REQUIRED_FIELDS:
        if field not in recipe:
            errors.append(f"MISSING required field: '{field}'")
    
    # 2. Check each field's DynamoDB type wrapper
    for field, value in recipe.items():
        if field not in EXPECTED_SCHEMA:
            # Extra fields that the pipeline adds but gold standard doesn't have
            # are warnings, not errors (Lambda may ignore them)
            if field not in ('createdAt', 'updatedAt', '_version', '_lastChangedAt'):
                warnings.append(f"Extra field '{field}' not in gold standard schema")
            continue
        
        expected_wrapper, expected_inner = EXPECTED_SCHEMA[field]
        
        # Must be a dict with exactly one key matching the expected wrapper
        if not isinstance(value, dict):
            errors.append(
                f"WRONG TYPE '{field}': expected dict with '{expected_wrapper}' wrapper, "
                f"got {type(value).__name__}: {repr(value)[:80]}"
            )
            continue
        
        keys = list(value.keys())
        if len(keys) != 1:
            errors.append(
                f"WRONG WRAPPER '{field}': expected single key '{expected_wrapper}', "
                f"got keys {keys}"
            )
            continue
        
        actual_wrapper = keys[0]
        if actual_wrapper != expected_wrapper:
            errors.append(
                f"WRONG WRAPPER '{field}': expected '{expected_wrapper}', "
                f"got '{actual_wrapper}'"
            )
            continue
        
        inner_value = value[actual_wrapper]
        
        # 3. For list fields, check inner type
        if expected_wrapper == 'L' and expected_inner:
            if not isinstance(inner_value, list):
                errors.append(f"'{field}' L wrapper must contain a list, got {type(inner_value).__name__}")
                continue
            
            for i, item in enumerate(inner_value):
                if not isinstance(item, dict):
                    errors.append(
                        f"'{field}'[{i}]: expected dict with '{expected_inner}' wrapper, "
                        f"got {type(item).__name__}"
                    )
                    break
                item_keys = list(item.keys())
                if len(item_keys) != 1 or item_keys[0] != expected_inner:
                    errors.append(
                        f"'{field}'[{i}]: expected single key '{expected_inner}', "
                        f"got keys {item_keys}"
                    )
                    break
        
        # 4. Type-specific value checks
        if expected_wrapper == 'S':
            if not isinstance(inner_value, str):
                errors.append(f"'{field}' S value must be str, got {type(inner_value).__name__}")
        elif expected_wrapper == 'N':
            if not isinstance(inner_value, str):
                errors.append(f"'{field}' N value must be str (number as string), got {type(inner_value).__name__}")
        elif expected_wrapper == 'BOOL':
            if not isinstance(inner_value, bool):
                errors.append(f"'{field}' BOOL value must be bool, got {type(inner_value).__name__}")
        elif expected_wrapper == 'M':
            if not isinstance(inner_value, dict):
                errors.append(f"'{field}' M value must be dict, got {type(inner_value).__name__}")
    
    # 5. Business logic checks (only run if structural checks passed)
    #    These safely extract values from DynamoDB-typed wrappers.
    def _get_s(field): 
        v = recipe.get(field)
        return v.get('S', '') if isinstance(v, dict) and 'S' in v else ''
    def _get_bool(field):
        v = recipe.get(field)
        return v.get('BOOL', None) if isinstance(v, dict) and 'BOOL' in v else None
    def _get_list(field):
        v = recipe.get(field)
        return v.get('L', []) if isinstance(v, dict) and 'L' in v else []
    
    ct = _get_s('cuisineType')
    if ct and ct not in VALID_CUISINES:
        errors.append(f"Invalid cuisineType: '{ct}' (valid: {VALID_CUISINES})")
    
    dt = _get_s('dishType')
    if dt and dt not in VALID_DISH_TYPES:
        errors.append(f"Invalid dishType: '{dt}' (valid: {VALID_DISH_TYPES})")
    
    # Check time flag mutual exclusivity
    time_flags = {}
    for flag in ('isQuick', 'isBalanced', 'isGourmet'):
        val = _get_bool(flag)
        if val is not None:
            time_flags[flag] = val
    
    true_flags = [k for k, v in time_flags.items() if v]
    if len(true_flags) != 1 and len(time_flags) == 3:
        errors.append(f"Exactly ONE time flag must be true, got: {true_flags}")
    
    # Check main dishes have primary=true
    if dt == 'main' and _get_bool('primary') is not True:
        errors.append(f"Main dish must have primary=true, got {_get_bool('primary')}")
    
    # Check recommendedSides not empty for main dishes
    if dt == 'main' and len(_get_list('recommendedSides')) == 0:
        warnings.append("Main dish has empty recommendedSides")
    
    # Check products not empty for main dishes
    if dt == 'main' and len(_get_list('products')) == 0:
        warnings.append("Main dish has empty products")
    
    is_valid = len(errors) == 0
    return is_valid, errors, warnings


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Validate EZ Meals S3 recipe JSON format')
    parser.add_argument('file', nargs='?', help='Local JSON file to validate')
    parser.add_argument('--check-s3', metavar='KEY', help='S3 key in menu-items-json bucket to validate')
    parser.add_argument('--quiet', action='store_true', help='Only show errors')
    args = parser.parse_args()
    
    if args.check_s3:
        import boto3
        sts = boto3.client('sts')
        creds = sts.assume_role(
            RoleArn='arn:aws:iam::970547358447:role/CrossAccountDynamoDBWriter',
            RoleSessionName='validate'
        )['Credentials']
        s3 = boto3.client('s3', region_name='us-west-1',
            aws_access_key_id=creds['AccessKeyId'],
            aws_secret_access_key=creds['SecretAccessKey'],
            aws_session_token=creds['SessionToken'])
        obj = s3.get_object(Bucket='menu-items-json', Key=args.check_s3)
        recipe = json.loads(obj['Body'].read().decode())
        source = f"s3://menu-items-json/{args.check_s3}"
    elif args.file:
        with open(args.file) as f:
            recipe = json.load(f)
        source = args.file
    else:
        parser.print_help()
        sys.exit(1)
    
    is_valid, errors, warnings = validate_s3_recipe(recipe)
    
    if not args.quiet:
        print(f"\n{'='*60}")
        print(f"  Validating: {source}")
        print(f"{'='*60}")
        print(f"  Fields: {len(recipe)}")
        title_val = recipe.get('title', {})
        title_str = title_val.get('S', 'N/A') if isinstance(title_val, dict) else str(title_val)
        print(f"  Title: {title_str}")
    
    if errors:
        print(f"\n❌ FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  ❌ {e}")
    
    if warnings and not args.quiet:
        print(f"\n⚠️  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  ⚠️  {w}")
    
    if is_valid:
        print(f"\n✅ PASSED — valid DynamoDB-typed format")
    
    sys.exit(0 if is_valid else 1)


if __name__ == '__main__':
    main()
