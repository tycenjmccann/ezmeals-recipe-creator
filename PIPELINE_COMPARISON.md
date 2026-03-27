# Pipeline Comparison: Step Functions vs Strands Graph

## Architecture

| | Step Functions | Strands Graph (Current) |
|---|---|---|
| **Nodes** | 6 sequential Lambdas + URL scraper | 5 graph nodes |
| **Model** | Claude Sonnet (via `converse` API) | Mixed (Haiku/Sonnet/Opus) |
| **State passing** | JSON payload between steps | Graph state (node results) |
| **Cost** | ~$0.15-0.30/recipe (all Sonnet) | ~$1-2/recipe (2 Opus nodes) |
| **Time** | ~2 min | ~2.5 min |
| **Error handling** | Lambda retries + auto-fix validation | None currently |

## Step-by-Step Comparison

### Step 0: URL Scraping
**SF:** Separate Lambda (`ez-recipe-url-scraper`) → feeds text to pipeline
**Graph:** `scraper` node (Haiku + scrape_recipe_url tool)
**Gap:** ✅ Equivalent — both extract recipe text

### Step 1: Recipe Text → DynamoDB JSON (`ez-text-input-to-json`)
**SF Prompt:** EXTREMELY detailed. Specifies:
- Every single field name with exact DynamoDB type (`"S"`, `"N"`, `"BOOL"`, `"L"`, `"M"`)
- Exact image URL format: `menu-item-images/[recipe_name].[extension]`
- Exact time logic: 0-30=isQuick, 35-60=isBalanced, >60=isGourmet
- Instruction format: imperative verbs, beginner-friendly, include quantities
- Ingredient format: mixed fractions (not 0.5), no special characters
- Notes limited to GF substitutions + dietary variations only
- Auto-fix validation function catches & repairs common errors

**Graph:** `converter` node — has the schema but LESS detail on:
- ❌ No auto-fix validation (validate_recipe_json is called but schema conversion is fragile)
- ❌ Missing the `imageURL` format spec (menu-item-images/Recipe_Name.jpg)
- ❌ Missing `link` field extraction from recipe text
- ❌ Missing `baseMainId`, `primary` flag logic
- ❌ Missing notes restriction (GF/dietary only)
- ❌ Missing instruction format requirements (imperative verbs, beginner-friendly)

**GAP: LARGE** — Step 1 is the most complex and detailed prompt. Our graph converter is underpowered.

### Step 2: Ingredient Standardization (`ez-standardize-ingredients-update-json`)
**SF:** Dedicated Lambda with:
- Cross-account DynamoDB lookup of standardized ingredients
- Focused prompt: ONLY standardize names and units
- 7 critical rules (preserve quantities, preserve prep instructions, etc.)
- Change tracking: shows before/after for each ingredient
- Smart filtering: only sends relevant standardized names

**Graph:** `converter` node calls `get_standardized_ingredients` tool
**Gap:** ⚠️ MEDIUM — Same tool called, but:
- ❌ Change tracking not implemented
- ❌ No smart filtering of standardized names
- ❌ Combined with JSON conversion instead of separate step

### Step 3: Ingredient Objects (`ez-create-ingredientsObject-json-update`)
**SF:** Dedicated Lambda with:
- Field-specific processing (sends ONLY ingredients, not full JSON)
- Exact DynamoDB format specified: `{"M": {"ingredient_name": {"S": "..."}...}}`
- Parsing examples for edge cases
- Bulletproof merge back into recipe JSON

**Graph:** `converter` node tries to do this inline
**Gap:** ⚠️ MEDIUM — Same logic needed but:
- ❌ No dedicated step — mixed into converter
- ❌ No field-specific context window optimization
- ❌ No merge validation

### Step 4: Side Dish Recommendations (`ez-recommend-sides-placeholder`)
**SF:** Dedicated Lambda with:
- Cross-account DynamoDB scan of ALL side dishes (complete info: id, title, description, cuisine, ingredients)
- Multi-criteria evaluation: flavor, texture, nutrition, color, cooking method synergy
- Allows cross-cuisine pairings
- Returns 3-6 IDs as JSON array

**Graph:** `enricher` node calls `get_available_sides` tool
**Gap:** ✅ EQUIVALENT — Same tool, similar prompt. Minor differences in evaluation criteria wording.

### Step 5: Affiliate Products (`ez-add-affiliate-products-json-update`)
**SF:** Dedicated Lambda with:
- Cross-account DynamoDB scan of ALL products (~62 items, 8 attributes each)
- Food/ingredient EXCLUSION rule (tools/equipment only)
- Complete product info: name, description, imageURL, affiliateLink, price, ASIN, category
- Quality criteria: explicitly mentioned equipment, technique improvement, cuisine-specific

**Graph:** `enricher` node calls `get_available_products` tool
**Gap:** ✅ MOSTLY EQUIVALENT — Same tool. Missing:
- ⚠️ Food exclusion not as explicit in prompt

### Step 6: QA Review (`ez-recipe-QA`)
**SF:** Dedicated Lambda with:
- Side-by-side comparison: original recipe text vs final JSON
- Processing notes review (shows all changes from steps 1-5)
- Quality gate: "Publish" or "Review Needed" with reasons
- Product/side dish validation against recipe context
- GF, vegetarian, slow cooker, instapot attribute verification
- 2048 max tokens for concise output

**Graph:** `qa_review` node (Opus)
**Gap:** ⚠️ MEDIUM — Missing:
- ❌ **No access to original recipe text** (critical — can't compare original vs final)
- ❌ No processing notes trail
- ❌ Doesn't receive the converter's JSON output (graph state issue!)
- ❌ Less structured output format

## Critical Gaps to Fix

### Priority 1 — Graph State Passing
The QA node doesn't receive the converter's output. Each node only sees the PREVIOUS node's output, not all nodes. Need to either:
- Pass state explicitly through the graph
- Or accumulate results in a shared state dict

### Priority 2 — Step 1 Prompt (Converter)
The converter prompt needs ALL of these from the SF Step 1:
- Exact field list with DynamoDB types
- `imageURL` format: `menu-item-images/{Recipe_Name}.{ext}`
- `link` field: extract URL from recipe text
- `primary` = true for main, false for side
- `baseMainId` = ""
- Time logic: 0-30=Quick, 35-60=Balanced, >60=Gourmet (mutually exclusive!)
- Instruction requirements: imperative verbs, quantities, beginner-friendly
- Notes: GF substitutions and dietary variations ONLY
- Mixed fractions, no special characters

### Priority 3 — Separate Step 2 & Step 3
Currently the converter tries to do Steps 1+2+3 in one pass. Should be:
- Node A: Text → DynamoDB JSON (Step 1)
- Node B: Standardize ingredients (Step 2)
- Node C: Create ingredient objects (Step 3)
This matches the SF's context-window optimization strategy.

### Priority 4 — Auto-fix Validation
SF Step 1 has a `validate_and_fix_response_schema()` function that catches:
- Wrong time flag logic
- Missing/malformed imageURLs
- Missing required fields
Our `validate_recipe_json` tool has this but it's not being called effectively.

## Recommendation

**Mirror the Step Functions pipeline exactly** — 7 graph nodes instead of 5:
1. `scraper` (Haiku) — scrape URL
2. `chef_review` (Opus) — culinary analysis
3. `json_converter` (Sonnet) — text to DynamoDB JSON with FULL SF Step 1 prompt
4. `ingredient_standardizer` (Haiku) — call get_standardized_ingredients, update JSON
5. `ingredient_objects` (Haiku/Sonnet) — parse ingredients into structured objects
6. `enricher` (Sonnet) — sides + products
7. `qa_review` (Opus) — full QA with access to ALL previous node outputs

The chef_review is the one addition over SF. Everything else should match SF step-for-step with identical prompts.
