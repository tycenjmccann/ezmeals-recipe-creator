# EZmeals Recipe Creator — Architecture & Specification

## Overview

AI-powered recipe processing pipeline built on [Strands Agents SDK](https://github.com/strands-agents/sdk-python). Takes a recipe URL as input and produces a fully processed, validated recipe with branded images — ready for DynamoDB insertion.

Replaces the previous AWS Step Functions workflow (6 Lambda functions) with a single orchestrator agent that calls 10 specialist agents/tools.

## Architecture: Agents as Tools

```
User provides URL
    → Orchestrator Agent (has 10 @tool functions)
        → scrape_recipe(url)
        → chef_review(text) — culinary analysis + web comparison + optimization
        → convert_to_json(optimized_text, id)
        → standardize_ingredients(json)
        → create_ingredient_objects(json)
        → recommend_sides(title, desc, ingredients, cuisine)
        → recommend_products(title, desc, instructions)
        → [ORCHESTRATOR ASSEMBLES FINAL JSON]
        → qa_review(original, final_json, summary)
        → generate_recipe_images(name, desc, image_path, prefix)
        → publish_recipe(json, hero_path, thumb_path, id) — S3 + DynamoDB + local
    → Recipe live in app
```

Each `@tool` function creates a specialist `Agent` with its own tools and prompt, invokes it, and returns the result string to the orchestrator. The orchestrator is the single source of truth — it integrates each specialist's output into the recipe JSON between calls.

## File Structure

| File | Purpose |
|---|---|
| `recipe_graph.py` | **Entry point.** Orchestrator agent + 8 `@tool`-wrapped specialists. Run with: `python3 recipe_graph.py "<url>"` |
| `strands_agents_recipe_creator.py` | **Library.** Custom `@tool` definitions, AWS config, agent definitions (reference copies), validation logic, image generation |
| `.env` | `GOOGLE_API_KEY` for Gemini image generation (gitignored) |

## Running the Pipeline

```bash
cd RecipeCreator
AWS_DEFAULT_REGION=us-west-2 python3 recipe_graph.py "https://example.com/recipe-url"
```

Requirements:
- AWS credentials configured (profile `ezmeals` or cross-account role)
- `GOOGLE_API_KEY` in `.env` for image generation
- Python packages: `strands-agents`, `strands-agents-tools`, `google-genai`, `requests`, `beautifulsoup4`

---

## Agent Specifications

### Agent 0: URL Scraper

| | |
|---|---|
| **Purpose** | Extract recipe text and hero image from a URL |
| **Tools** | `scrape_recipe_url` |
| **Input** | Recipe URL |
| **Output** | Recipe text (title, description, ingredients, instructions, cuisine) + downloaded image path |
| **Extraction** | JSON-LD structured data first, HTML fallback second |
| **Known limitation** | Some sites have empty `recipeInstructions` in JSON-LD (e.g., iankewks.com) — the downstream agent compensates with culinary knowledge |

### Agent 0.5: Chef Review

| | |
|---|---|
| **Purpose** | Culinary analysis, web comparison, and recipe optimization before processing |
| **Tools** | `http_request` (web research) |
| **Input** | Raw scraped recipe text |
| **Output** | Culinary assessment + optimized recipe text + change log |
| **Web research** | Searches for 2-3 similar recipes, compares ratios/techniques/steps |

**What it checks:**
- Ingredient ratios (enough seasoning for protein weight?)
- Cook times and temperatures (realistic and safe?)
- Missing steps (resting meat, preheating, food safety)
- Completeness (were steps lost in scraping?)

**Output feeds into Agent 1** — the orchestrator passes the optimized recipe text (not the raw scrape) to the JSON converter.

### Agent 1: Text to JSON Converter

| | |
|---|---|
| **Purpose** | Convert raw recipe text into DynamoDB-compatible JSON |
| **Tools** | `validate_recipe_json` |
| **Input** | Recipe text + unique ID |
| **Output** | Complete DynamoDB JSON with all required fields |
| **Prompt source** | Exact copy from `step1_ez-text-input-to-json_FINAL.py` |
| **Post-processing** | Agent MUST call `validate_recipe_json` after generating JSON — auto-fixes metadata errors |

**Required JSON fields:**

| Field | Type | Notes |
|---|---|---|
| `id` | `S` | Unique slug (e.g., `thai-crying-tiger-steak`) |
| `title` | `S` | Exact recipe name |
| `dishType` | `S` | `main` or `side` |
| `primary` | `BOOL` | `true` for mains, `false` for sides |
| `imageURL` | `S` | `menu-item-images/Recipe_Name.jpg` |
| `imageThumbURL` | `S` | `menu-item-images/Recipe_Name_thumbnail.jpg` |
| `description` | `S` | Engaging summary with cuisine reference |
| `prepTime` / `cookTime` | `N` | Minutes |
| `servings` | `S` | e.g., `"4"`, `"6-8"` |
| `cuisineType` | `S` | See [Allowed Values](#allowed-values) |
| `isQuick` / `isBalanced` / `isGourmet` | `BOOL` | Mutually exclusive time flags |
| `ingredients` | `L` | List of `{"S": "..."}` strings |
| `instructions` | `L` | List of `{"S": "..."}` — imperative verbs, quantities included |
| `notes` | `L` | GF substitutions, dietary variations only |
| `glutenFree` | `BOOL` | Always `true` (with substitution notes) |
| `vegetarian` | `BOOL` | Based on ingredients |
| `slowCook` / `instaPot` | `BOOL` | Based on cooking method |
| `flagged` | `BOOL` | Always `false` |
| `ingredient_objects` | `L` | Populated by Agent 3 |
| `recommendedSides` | `L` | Populated by Agent 4 |
| `products` | `L` | Populated by Agent 5 |

### Agent 2: Ingredient Standardizer

| | |
|---|---|
| **Purpose** | Standardize ingredient names and units against the master Ingredient table |
| **Tools** | `get_standardized_ingredients` |
| **Input** | Recipe JSON (ingredients list) |
| **Output** | Updated ingredients list + change summary |
| **Prompt source** | Exact copy from `step2_ez-standardize-ingredients_FINAL.py` |
| **DB access** | Read-only scan of `Ingredient-*-dev` table, filtered by core ingredient names |

**Standardization rules:**
- Capitalize core names: `soy sauce` → `Soy Sauce`
- Standardize units: `lbs` → `pounds`, `tsp` → `teaspoon`, `tbsp` → `tablespoon`
- Preserve quantities exactly (no rounding, no conversion)
- Preserve all preparation instructions exactly as written
- If no match in DB, leave unchanged

### Agent 3: Ingredient Objects Creator

| | |
|---|---|
| **Purpose** | Parse ingredient strings into structured DynamoDB objects |
| **Tools** | None (LLM-only, same as original Step Function) |
| **Input** | Recipe JSON (ingredients list) |
| **Output** | `ingredient_objects` list in DynamoDB format |
| **Prompt source** | Exact copy from `step3_ez-create-ingredientsObject_FINAL.py` |

**Object structure:**
```json
{
  "M": {
    "ingredient_name": {"S": "Yellow Onion"},
    "category": {"S": "Produce"},
    "quantity": {"S": "1"},
    "unit": {"S": "cup"},
    "note": {"S": "chopped"},
    "affiliate_link": {"S": ""}
  }
}
```

### Agent 4: Side Dish Recommender

| | |
|---|---|
| **Purpose** | Recommend 3-6 side dishes from our catalog + identify gaps |
| **Tools** | `get_available_sides`, `http_request` |
| **Input** | Recipe title, description, ingredients, cuisine type |
| **Output** | List of side dish IDs + new side suggestions |
| **Prompt source** | Exact copy from `step4_ez-recommend-sides_FINAL.py` |
| **DB access** | Read-only scan of `MenuItemData-*-dev` where `dishType=side` |
| **Web research** | Searches for popular pairings and identifies catalog gaps |

### Agent 5: Affiliate Products Recommender

| | |
|---|---|
| **Purpose** | Recommend non-food affiliate products (tools, equipment) + identify gaps |
| **Tools** | `get_available_products`, `http_request` |
| **Input** | Recipe title, description, instructions |
| **Output** | List of product IDs + new product suggestions |
| **Prompt source** | Exact copy from `step5_ez-add-affiliate-products_FINAL.py` |
| **DB access** | Read-only scan of `AffiliateProduct-*-dev` |
| **Restriction** | NO food items — tools, equipment, cookware only |

### Agent 6: Quality Assurance

| | |
|---|---|
| **Purpose** | Validate final recipe against comprehensive checklist |
| **Tools** | `http_request` (for link verification) |
| **Input** | Original recipe text, final assembled JSON, processing summary |
| **Output** | PUBLISH or REVIEW NEEDED + checklist results + required fixes |
| **Prompt source** | Exact copy from `step6_ez-recipe-QA_FINAL.py` + enhanced checklist |

**QA Checklist:**
1. Instructions — quantities in every step, imperative verbs, beginner-friendly
2. Ingredients — fractions not decimals, standardized units, no special chars
3. Ingredient objects — populated, correct categories, capitalized names
4. Metadata — valid cuisineType, correct time flags, imageURL format
5. Side dishes — populated with valid IDs
6. Products — non-food items only
7. Notes — GF substitutions, dietary variations

### Agent 7: Image Generator

| | |
|---|---|
| **Purpose** | Generate branded recipe images from the scraped hero photo |
| **Tools** | `generate_recipe_images` |
| **Input** | Dish name, visual description, source image path, output prefix |
| **Output** | Hero image (16:9) + thumbnail (1:1) file paths |
| **Model** | Google Gemini `gemini-3.1-flash-image-preview` |
| **API key** | `GOOGLE_API_KEY` from `.env` |

**2-step generation:**
1. Edit source photo → 16:9 landscape hero (EZ Meals brand style)
2. Reframe hero → 1:1 square thumbnail

**Brand style:** Clean, bright food photography. Warm natural lighting from left/above. Simple background (light wood, marble, white). Dish centered at 60-70%. No text/logos/watermarks/hands/people.

---

## Custom @tool Definitions

### `validate_recipe_json`
Auto-fixes common metadata errors in recipe JSON:
- Invalid `cuisineType` → defaults to `Global Cuisines`
- Invalid `dishType` → defaults to `main`
- `primary` flag mismatch → synced to dishType
- Time flags recalculated from `prepTime + cookTime`
- `imageURL` / `imageThumbURL` missing `menu-item-images/` prefix → added
- `flagged` forced to `false`
- Missing fields (`recommendedSides`, `products`, etc.) → added with defaults

### `get_standardized_ingredients`
- Scans `Ingredient-ryvykzwfevawxbpf5nmynhgtea-dev` table
- Accepts comma-separated ingredient names
- Returns matching records with `ingredient_name`, `standardized_name`, `unit`, `category`
- Read-only, cross-account via STS AssumeRole

### `get_available_sides`
- Scans `MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev` where `dishType=side`
- Returns `id`, `title`, `description`, `cuisineType`, `ingredients`
- Read-only

### `get_available_products`
- Scans `AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev`
- Returns `id`, `productName`, `description`, `link`, `usedInMenuItem`
- Read-only

### `scrape_recipe_url`
- Fetches page HTML, extracts via JSON-LD first, HTML fallback second
- Downloads hero image to `/tmp/`
- Returns structured recipe text + image path

### `generate_recipe_images`
- 2-step Gemini flow: hero (16:9) → thumbnail (1:1)
- Model: `gemini-2.5-flash-image` (paid tier, `ai-generator-cli` project)
- Preserves real food from source photo, adjusts composition/lighting/background
- Requires `GOOGLE_API_KEY` + `GOOGLE_CLOUD_PROJECT` in `.env`

### `publish_recipe`
- Saves local copy to `RecipeCreator/output/<recipe-id>/` (JSON + images + publish log)
- Uploads hero + thumbnail to S3 (`amplify-ezmealsnew-menu-item-imageseb66c-dev/public/menu-item-images/`)
- Writes recipe to DynamoDB (`MenuItemData` table) via cross-account role
- Converts DynamoDB JSON format (`{"S": "val"}`) to plain format for boto3 Table resource
- Always publishes — pipeline only completes if QA passes

---

## Allowed Values

### Cuisine Types
```
Global Cuisines, American, Asian, Indian, Italian, Latin, Soups & Stews
```
No other values are valid. "French" → `Global Cuisines`. "Mexican" → `Latin`. "Thai" → `Asian`.

### Ingredient Object Categories
```
Produce, Proteins, Dairy, Grains & Bakery, Pantry Staples, Seasonings, Frozen Foods
```

### Time Flags (mutually exclusive)
| Total Time | isQuick | isBalanced | isGourmet |
|---|---|---|---|
| 0–30 min | `true` | `false` | `false` |
| 31–60 min | `false` | `true` | `false` |
| > 60 min | `false` | `false` | `true` |

### Dish Types
```
main, side
```
`primary` = `true` when `dishType` = `main`, `false` when `side`.

---

## AWS Resources

| Resource | Table / Bucket | Region | Account |
|---|---|---|---|
| Menu items | `MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev` | us-west-1 | 970547358447 |
| Ingredients | `Ingredient-ryvykzwfevawxbpf5nmynhgtea-dev` | us-west-1 | 970547358447 |
| Products | `AffiliateProduct-ryvykzwfevawxbpf5nmynhgtea-dev` | us-west-1 | 970547358447 |
| S3 images | `amplify-ezmealsnew-menu-item-imageseb66c-dev` → `public/menu-item-images/` | us-west-1 | 970547358447 |
| Cross-account role | `arn:aws:iam::970547358447:role/IsengardAccount-DynamoDBAccess` | — | — |
| Bedrock model | `us.anthropic.claude-opus-4-6-v1` | us-west-2 | 023392223961 |

**DB access pattern:** STS AssumeRole → cross-account DynamoDB read. Fallback: `ezmeals` AWS profile.

All database access is **read-only**. No agent writes to DynamoDB. The pipeline outputs JSON locally.

---

## Comparison: Step Functions → Strands Agents

| Step Function | Strands Agent | DB Access | New Capabilities |
|---|---|---|---|
| *(manual URL input)* | Agent 0: URL Scraper | — | 🆕 Automated scraping |
| *(no culinary review)* | Agent 0.5: Chef Review | — | 🆕 Culinary analysis, web comparison, recipe optimization |
| `step1_ez-text-input-to-json` | Agent 1: Text to JSON | — | 🆕 `validate_recipe_json` auto-fix |
| `step2_ez-standardize-ingredients` | Agent 2: Standardizer | `Ingredient` table (read) | ✅ Same |
| `step3_ez-create-ingredientsObject` | Agent 3: Objects Creator | — | ✅ Same |
| `step4_ez-recommend-sides` | Agent 4: Side Recommender | `MenuItemData` (read) | 🆕 Web research + gap analysis |
| `step5_ez-add-affiliate-products` | Agent 5: Products | `AffiliateProduct` (read) | 🆕 Web research + gap analysis |
| `step6_ez-recipe-QA` | Agent 6: QA | — | 🆕 Enhanced checklist + link verification |
| *(manual image creation)* | Agent 7: Image Generator | — | 🆕 Gemini branded images |
| *(manual DB entry)* | `publish_recipe` tool | `MenuItemData` (write), S3 (write) | 🆕 Auto-publish to S3 + DynamoDB |

---

## Prompt Policy

All agent system prompts for steps 1-6 are copied **word-for-word** from the original Step Function Lambda code (`StepFunctionUpdates/step*_FINAL.py`). The only additions are:
- Tool usage instructions (e.g., "FIRST: Call get_available_sides...")
- Enhanced QA checklist with exact allowed values
- Web research instructions for Agents 4 and 5
- New agents (0, 0.5, 7) and tools (publish_recipe) have original prompts

**Do not rewrite Step Function prompts.** If a prompt needs changes, update the Step Function source file first, then copy to the agent definition.

---

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| `ResourceNotFoundException` on startup | Cross-account role assumption failed | Check AWS credentials / `ezmeals` profile |
| Image generation `API key not valid` | Wrong env var name or key from wrong GCP project | Use `GOOGLE_API_KEY` (not `GEMINI_API_KEY`), key must be from `ai-generator-cli` project (paid tier) |
| Image generation `429 RESOURCE_EXHAUSTED` | Free tier project has `limit: 0` for image models | Must use paid tier project (`ai-generator-cli`). Free tier AI Studio keys don't have image gen quota |
| Image generation `Thinking is not enabled` | `thinking_config` passed to a model that doesn't support it | Don't pass `thinking_config` to `gemini-2.5-flash-image` |
| Empty instructions from scraper | Site's JSON-LD has no `recipeInstructions` | Chef Review agent (0.5) compensates by writing full instructions from culinary knowledge + web research |
| `mutative operation` prompt hangs | An agent has `use_aws` tool and tried to write | Remove `use_aws` from agent tools — all DB access is via custom read-only `@tool` functions |
| DynamoDB publish `SameFileError` | Image paths already point to output dir | Fixed — publish tool skips copy when src == dst |
| Wrong cuisineType in output | Agent guessed instead of using allowed list | `validate_recipe_json` auto-fixes this |
| Time flag mismatch | Manual calculation error | `validate_recipe_json` recalculates from prepTime + cookTime |

---

## Dependencies

```
strands-agents          # Core SDK
strands-agents-tools    # http_request, journal, workflow, agent_graph
google-genai            # Gemini image generation
Pillow                  # Image processing (required by google-genai for .as_image())
requests                # HTTP for scraping
beautifulsoup4          # HTML parsing
boto3                   # AWS SDK (DynamoDB, STS, Bedrock)
```
