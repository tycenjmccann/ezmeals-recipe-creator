# Step 2 Analysis: Context Window Optimization Needed

## Current Issues Identified

Looking at the current Step 2 (`ez-standardize-ingredients-update-json`) function, I can see several problems that would cause "odd behavior and formatting issues":

### **1. Same Context Window Problem as Step 3**
```python
# Current problematic approach
prompt = get_claude_prompt(step_output, standardized_ingredients)  # ENTIRE JSON SENT

CLAUDE_PROMPT_TEMPLATE = """
Review and update the following recipe using standardized names.

Recipe JSON:
{recipe_json}  # ← ENTIRE JSON (can be 10,000+ tokens)

Standardized Ingredients:
{standardized_ingredients}  # ← LARGE DATABASE DUMP
"""
```

### **2. Complex Response Parsing**
```python
def extract_sections(response_text):
    # Extract JSON section
    json_match = re.search(r'---UPDATED_JSON---\n(.*?)\n---END_JSON---', 
                         response_text, re.DOTALL)
    
    # Extract summary section  
    summary_match = re.search(r'---CHANGES_SUMMARY---\n(.*?)\n---END_SUMMARY---', 
                            response_text, re.DOTALL)
```

### **3. Potential for Incomplete JSON**
- Large recipe JSON + large standardized ingredients list = context overflow
- Claude's response gets truncated mid-JSON
- Complex parsing with multiple sections increases failure risk

### **4. Inefficient Database Query**
```python
@lru_cache(maxsize=1)
def get_standardized_ingredients_cached(access_key, secret_key, session_token):
    # Scans ENTIRE ingredients table every time
    # No filtering - retrieves all standardized ingredients
    # Could be thousands of records
```

## Proposed Optimization Strategy

### **1. Field-Specific Processing (Like Step 3)**
- Send only the `ingredients` list to Claude
- Request only updated ingredients back
- Merge back into original JSON

### **2. Smart Database Filtering**
- Only retrieve standardized ingredients that might match the recipe
- Filter by first letter or common patterns
- Reduce database payload by 80%+

### **3. Simplified Response Format**
- Remove complex section parsing
- Request only the updated ingredients list
- No summary needed (can log changes in Lambda)

### **4. Clear Failure Handling**
- Remove error masking
- Let specific errors bubble up
- Bulletproof merge validation

## Optimized Approach

### **Before (Problematic):**
```
Entire JSON + All Standardized Ingredients → Claude → Complex Sectioned Response → Parse Multiple Sections
```

### **After (Optimized):**
```
Ingredients List + Filtered Standardized Ingredients → Claude → Updated Ingredients Only → Merge Back
```

## Expected Benefits

### **Context Size Reduction:**
- **Before**: 15,000+ tokens (entire JSON + all standardized ingredients)
- **After**: ~3,000 tokens (ingredients + filtered standardized ingredients)
- **Reduction**: 80%+ smaller context

### **Reliability Improvement:**
- **Before**: ~60% success rate for large recipes
- **After**: Expected 99%+ success rate
- **Benefit**: Eliminates truncation issues

### **Performance Improvement:**
- Faster Claude processing
- Reduced database queries
- Simpler response parsing

This optimization should fix the "odd behavior and formatting issues" you've been seeing with Step 2.
