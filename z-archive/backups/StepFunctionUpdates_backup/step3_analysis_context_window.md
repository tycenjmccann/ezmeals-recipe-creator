# Step 3 Context Window Issue Analysis

## Current Problem
The current Step 3 function sends the entire JSON to Claude and asks it to return the complete JSON with `ingredient_objects` added. For large recipes with many ingredients, this causes:

1. **Context Window Overflow**: The full JSON + prompt exceeds Claude's output token limit
2. **Incomplete Responses**: Claude's response gets cut off mid-JSON
3. **JSON Parsing Errors**: Incomplete JSON causes parsing failures and workflow crashes

## Current Approach (Problematic)
```python
def create_claude_prompt(existing_json):
    return f"""
    You are provided with a JSON representing a recipe...
    
    Existing JSON:
    {json.dumps(existing_json, indent=2)}  # ENTIRE JSON SENT
    """
    
# Claude returns entire JSON - can be truncated
response = invoke_claude(prompt)
updated_body = json.loads(response)  # FAILS if truncated
```

## Proposed Solution: Field-Specific Processing

### 1. **Send Only Necessary Data to Claude**
Instead of sending the entire JSON, send only:
- The `ingredients` list (what Claude needs to process)
- Basic recipe context (title, cuisine type for categorization hints)

### 2. **Request Only the Target Field**
Ask Claude to return only the `ingredient_objects` structure, not the entire JSON.

### 3. **Merge in Lambda Function**
The Lambda function merges Claude's response back into the original JSON.

## Updated Approach (Optimized)

```python
def create_claude_prompt(ingredients_list, recipe_context):
    return f"""
    Create ingredient objects from this ingredients list.
    
    Recipe Context:
    - Title: {recipe_context.get('title')}
    - Cuisine: {recipe_context.get('cuisineType')}
    
    Ingredients to Process:
    {json.dumps(ingredients_list, indent=2)}
    
    Return ONLY the ingredient_objects structure in this format:
    {
        "L": [
            {
                "M": {
                    "ingredient_name": {"S": ""},
                    "category": {"S": ""},
                    "quantity": {"S": ""},
                    "unit": {"S": ""},
                    "note": {"S": ""},
                    "affiliate_link": {"S": ""}
                }
            }
        ]
    }
    """

def lambda_handler(event, context):
    # Extract only what Claude needs
    ingredients_list = step_output_data.get('ingredients', {}).get('L', [])
    recipe_context = {
        'title': step_output_data.get('title', {}).get('S', ''),
        'cuisineType': step_output_data.get('cuisineType', {}).get('S', '')
    }
    
    # Get only ingredient_objects from Claude
    prompt = create_claude_prompt(ingredients_list, recipe_context)
    claude_response = invoke_claude(prompt)
    ingredient_objects = json.loads(claude_response)
    
    # Merge back into original JSON
    step_output_data['ingredient_objects'] = ingredient_objects
    
    return {
        'statusCode': 200,
        'body': json.dumps(step_output_data)
    }
```

## Benefits of This Approach

### 1. **Dramatically Reduced Context Size**
- **Before**: Entire JSON (can be 10,000+ tokens)
- **After**: Just ingredients list + minimal context (typically <2,000 tokens)

### 2. **Focused Claude Instructions**
- Claude only needs to focus on ingredient parsing
- No risk of modifying other fields accidentally
- Clearer, more specific instructions

### 3. **Guaranteed Complete Responses**
- Much smaller output requirement
- Well within Claude's token limits
- Consistent, reliable parsing

### 4. **Better Error Handling**
- If Claude fails, original JSON is preserved
- Can implement fallbacks more easily
- Easier to debug specific field issues

## Implementation Changes Required

### 1. **Prompt Optimization**
```python
CLAUDE_PROMPT_TEMPLATE = """
Parse the following ingredients into structured objects.

Recipe Context (for categorization hints):
Title: {title}
Cuisine Type: {cuisine_type}

Ingredients to Parse:
{ingredients_json}

Instructions:
- Parse each ingredient into the exact DynamoDB format shown
- Move descriptors (large, fresh, chopped) to the note field
- Categorize using: {categories}
- Return ONLY the ingredient_objects structure, no other text

Expected Output Format:
{{
    "L": [
        {{
            "M": {{
                "ingredient_name": {{"S": "Ingredient Name"}},
                "category": {{"S": "Category"}},
                "quantity": {{"S": "Amount"}},
                "unit": {{"S": "Unit"}},
                "note": {{"S": "Preparation notes"}},
                "affiliate_link": {{"S": ""}}
            }}
        }}
    ]
}}
"""
```

### 2. **Response Processing**
```python
def extract_ingredient_objects(response_text):
    """Extract only the ingredient_objects structure"""
    # Look for the L array structure specifically
    json_match = re.search(r'{\s*"L":\s*\[.*?\]\s*}', response_text, re.DOTALL)
    if not json_match:
        raise ValueError("No ingredient_objects structure found")
    return json.loads(json_match.group(0))

def merge_ingredient_objects(original_json, ingredient_objects):
    """Safely merge ingredient_objects back into original JSON"""
    original_json['ingredient_objects'] = ingredient_objects
    return original_json
```

### 3. **Validation & Fallback**
```python
def validate_ingredient_objects(ingredient_objects, original_ingredients_count):
    """Validate the ingredient objects structure"""
    if not isinstance(ingredient_objects, dict) or 'L' not in ingredient_objects:
        raise ValueError("Invalid ingredient_objects structure")
    
    objects_count = len(ingredient_objects['L'])
    if objects_count == 0:
        raise ValueError("No ingredient objects created")
    
    # Log if counts don't match (not necessarily an error)
    if objects_count != original_ingredients_count:
        logger.warning(f"Ingredient count mismatch: {objects_count} objects vs {original_ingredients_count} ingredients")
    
    return True
```

## Rollback Plan

If the optimized version has issues:
1. **Revert to Version 1**: Update state machine to use `:1` version
2. **Increase timeout**: Try increasing Lambda timeout to 900 seconds
3. **Use smaller model**: Switch to Claude Haiku for faster processing

## Expected Results

- **Reliability**: 99%+ success rate vs current ~70% for large recipes
- **Performance**: Faster processing due to smaller context
- **Maintainability**: Clearer separation of concerns
- **Scalability**: Works with recipes of any size

This approach follows the same pattern we should apply to other steps that might face similar issues (Steps 4, 5, and our new side recommendation step).
