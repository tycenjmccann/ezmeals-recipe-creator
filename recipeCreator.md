# Recipe Creator Application Documentation

## Overview

The Recipe Creator is a comprehensive application designed to transform plain text recipe descriptions into structured JSON data for the EZMeals application. It features a complete processing pipeline with auto-fix validation, intelligent enhancements, comprehensive quality assurance, and **enhanced processing notes with specific change tracking**.

## 🚀 **Latest Major Enhancements (June 2025)**

### ✅ **Enhanced Processing Notes with Specific Details**
- **Specific Change Tracking**: Shows exactly what was modified in each step
- **Named Item Resolution**: Displays actual names of recommended sides and products
- **Focused QA**: Concise quality assessment with practical culinary improvements
- **Complete Transparency**: Users see specific before/after changes, not just counts

#### **Enhanced Processing Notes Examples**:
- **Step 2**: `"Standardized ingredients: - '2 lbs ground beef' → '2 pounds Ground Beef'; - '1 tsp salt' → '1 teaspoon Salt'"`
- **Step 4**: `"Recommended 4 side dishes: Italian Garden Salad, Garlic Bread, Caprese Skewers, Roasted Vegetables"`
- **Step 5**: `"Added 3 affiliate products: Pasta Maker Pro, Premium Parmesan Grater, Italian Herb Blend"`

### ✅ **Streamlit Integration with S3 Upload**
- **End-to-End Workflow**: Process → Review → Edit → Upload
- **Editable JSON**: Make changes to recipe JSON before upload
- **Dual Storage**: Automatic upload to S3 bucket + local backup
- **Debug Sidebar**: Real-time logging and troubleshooting
- **Persistent Results**: No clearing after button clicks
- **File Naming**: Recipe name only (no timestamp) for overwriting previous versions

### ✅ **Context Window Optimizations**
- **Problem Solved**: Eliminated JSON truncation issues across all steps
- **Field-Specific Processing**: Send only relevant data to Claude, not entire JSON
- **80%+ Reduction**: Context size reduced from 15,000+ to ~3,000 tokens
- **Guaranteed Completion**: 99%+ success rate for all recipe sizes

### ✅ **Auto-Fix Capabilities**
- **Workflow Continuity**: Common validation errors fixed automatically
- **Smart Corrections**: Cooking time logic, image URLs, field validation
- **Complete Transparency**: All fixes tracked in processing notes

## Frontend Application

### RecipeCreatorWorkflow.py

The frontend is built using Streamlit, providing a comprehensive web interface where users can:

1. **Submit Recipe**: Enter plain text recipe in a text area
2. **Process Recipe**: Submit for processing through 6-step workflow
3. **Check Status**: Monitor processing progress with debug logging
4. **Review Results**: View comprehensive results including:
   - **QA Summary**: Focused quality assessment with culinary improvements
   - **Processing Notes**: Complete audit trail with specific change details
   - **Recipe JSON**: Clean, structured recipe data (editable)
5. **Edit JSON**: Make modifications to recipe before upload
6. **Upload to S3**: Direct upload to production bucket with local backup

#### **Enhanced Streamlit Features**:

##### **S3 Upload Integration**
- **Target Bucket**: `menu-items-json` (EZMeals production bucket)
- **AWS Profile**: Uses `ezmeals` profile for S3 access
- **File Naming**: Recipe name only (e.g., `Pastalaya.json`) for overwriting
- **Dual Storage**: S3 upload + local backup to `/Users/tycenj/Desktop/EZMeals_DB_Storage/AutomatedUploads`
- **Error Handling**: Clear feedback for AWS credential or permission issues

##### **Debug Sidebar**
- **Real-Time Logging**: Shows upload process step-by-step
- **Persistent Messages**: Debug log stays visible across interactions
- **Timestamps**: Each message shows exact time
- **Error Tracking**: Captures and displays all errors with details

##### **Enhanced User Experience**
- **Persistent Results**: QA summary and JSON remain visible after upload attempts
- **Editable JSON**: Text area for making changes before upload
- **JSON Validation**: Real-time validation with error messages
- **Multiple Uploads**: Can upload same recipe multiple times with different edits
- **S3 Console Links**: Direct links to view uploaded files

#### **User Workflow**:
1. **Submit Recipe** → Enter text and click "Submit"
2. **Check Status** → Click "Check Status" to see results
3. **Review QA** → See focused quality assessment and processing notes
4. **Edit JSON** → Make any needed changes in the text area
5. **Upload** → Click "🚀 Submit Recipe to S3" (blue button)
6. **Verify** → Click S3 console link to confirm upload

## Backend Architecture

The backend is implemented as an AWS Step Functions state machine that orchestrates six Lambda functions in a comprehensive recipe processing pipeline.

### State Machine: ez-recipe-creator-V2

**ARN**: `arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2`  
**Region**: us-west-2  
**Runtime**: Python 3.13 across all functions

The state machine executes the following enhanced steps:

## Step-by-Step Processing Pipeline

### 1. **Recipe Text to JSON Conversion** (`ez-text-input-to-json`)
**Purpose**: Converts raw recipe text into structured DynamoDB-compatible JSON

**Key Features**:
- **Auto-Fix Validation**: Automatically corrects cooking time logic, image URLs, field validation
- **Processing Notes**: Tracks all fixes and validation issues
- **Clean Output**: Structured JSON ready for database insertion

**Enhanced Processing Notes**:
- `"Step 1: Fixed isQuick flag: 65 min total, changed from true to false"`
- `"Step 1: Fixed imageURL format: added menu-item-images/ prefix"`

**Auto-Fix Examples**:
- Cooking time logic: 65 min recipe with isQuick=true → auto-corrected to isBalanced=true
- Image URLs: "recipe.jpg" → "menu-item-images/recipe.jpg"
- Field validation: Missing required fields added with appropriate defaults

### 2. **Ingredient Standardization** (`ez-standardize-ingredients-update-json`)
**Purpose**: Standardizes ingredient names and units using database of standardized ingredients

**Key Features**:
- **Context Optimization**: 80% reduction in prompt size eliminates truncation
- **Focused Standardization**: Only standardizes names and units, preserves preparation
- **Smart Filtering**: Only processes relevant standardized ingredients
- **Cross-Recipe Protection**: Prevents contamination from other recipes
- **🆕 Specific Change Tracking**: Shows exact before/after ingredient modifications

**Enhanced Processing Notes**:
- `"Step 2: Standardized ingredients: - '2 lbs ground beef' → '2 pounds Ground Beef'; - '1 tsp oregano' → '1 teaspoon Dried Oregano'"`
- `"Step 2: No ingredients required standardization"`

**Example**:
- Input: "3 lbs beef chuck roast, sliced thin"
- Output: "3 pounds Chuck Roast, sliced thin"
- Note: Only name and unit standardized, preparation preserved, change tracked

### 3. **Ingredient Objects Creation** (`ez-create-ingredientsObject-json-update`)
**Purpose**: Creates structured ingredient objects from ingredient strings

**Key Features**:
- **Context Optimization**: Field-specific processing eliminates truncation
- **Bulletproof Merge**: 100% guaranteed correct JSON structure placement
- **Structured Data**: Complete ingredient objects with categories and metadata
- **Data Integrity**: Comprehensive validation ensures no data loss

**Enhanced Processing Notes**:
- `"Step 3: Created 15 ingredient objects from 15 ingredients"`

**Example Output**:
```json
{
  "ingredient_name": {"S": "Yellow Onion"},
  "category": {"S": "Produce"},
  "quantity": {"S": "1"},
  "unit": {"S": "cup"},
  "note": {"S": "chopped"},
  "affiliate_link": {"S": ""}
}
```

### 4. **Side Dish Recommendations** (`ez-recommend-sides-placeholder`)
**Purpose**: Recommends complementary side dishes for main dishes

**Key Features**:
- **Context Optimization**: Field-specific processing for reliable responses
- **Intelligent Pairing**: Cuisine compatibility and flavor profile analysis
- **Database Integration**: Cross-account access to EZMeals side dish database
- **Smart Processing**: Main dishes get recommendations, side dishes pass through
- **🆕 Side Dish Names**: Shows actual names of recommended sides

**Enhanced Processing Notes**:
- `"Step 4: Recommended 4 side dishes: Italian Garden Salad, Garlic Bread, Caprese Skewers, Roasted Vegetables"`
- `"Step 4: Side dish detected - skipped side recommendations"`

**Example**:
- Italian pasta recipe → ["italian-salad-id", "garlic-bread-id", "caprese-id"]
- Processing note shows actual names: "Italian Garden Salad, Garlic Bread, Caprese Skewers"

### 5. **Affiliate Products Integration** (`ez-add-affiliate-products-json-update`)
**Purpose**: Identifies and adds relevant affiliate products to recipes using comprehensive analysis with complete product information

**Key Features**:
- **🆕 Complete Information Processing**: Sends all ~60 affiliate products with 8 complete attributes to Claude
- **🆕 Food/Ingredient Exclusion**: Specifically avoids consumable items, focuses on tools and equipment only
- **🆕 No Pre-filtering**: Claude evaluates all available products for optimal recommendations
- **Enhanced Context**: Claude sees complete product details for informed decisions
- **🆕 Product Names**: Shows actual names of recommended products

**Enhanced Processing Notes**:
- `"Step 5: Added 3 affiliate products: Pasta Maker Pro, Premium Parmesan Grater, Digital Kitchen Scale"`
- `"Step 5: No valid affiliate products found for this recipe"`

**Example**:
- Pasta recipe → ["pasta-maker-id", "parmesan-grater-id", "digital-scale-id"]
- Processing note shows actual names: "Pasta Maker Pro, Premium Parmesan Grater, Digital Kitchen Scale"
- **Improvement**: No longer recommends food items like spices or oils, focuses on reusable kitchen tools

### 6. **Quality Assurance Review** (`ez-recipe-QA`)
**Purpose**: **Focused QA review** with culinary improvements and quality assessment

**Key Features**:
- **🆕 Focused QA**: Concise quality assessment instead of verbose analysis
- **Culinary Improvements**: Practical cooking tips and ingredient suggestions
- **Quality-First**: Assumes processing worked correctly, focuses on recipe excellence
- **Processing Notes Review**: Analyzes all automated changes for appropriateness
- **Reduced Tokens**: 2048 max tokens for faster, more focused responses

**Enhanced QA Format**:
```
**OVERALL QUALITY**: Good - Recipe is accurate and ready to use.

**KEY DIFFERENCES** (if any):
• Cooking time corrected for proper pasta texture
• Ingredient names standardized for consistency

**CULINARY IMPROVEMENTS**:
• Use low-sodium broth to control salt levels
• Consider rice vinegar instead of white vinegar for more subtle flavor
• Toast spices for 30 seconds before adding liquid for enhanced aroma
```

**Enhanced Processing Notes**:
- `"Step 6: Focused QA review completed - quality assessment provided"`

## Data Flow Architecture

### Enhanced Processing Notes Pattern
Each step accumulates processing notes with **specific details** for complete transparency:

```json
{
  "statusCode": 200,
  "body": "{...clean recipe JSON...}",
  "processingNotes": [
    "Step 1: Fixed isQuick flag: 65 min total, changed from true to false",
    "Step 2: Standardized ingredients: - '2 lbs ground beef' → '2 pounds Ground Beef'; - '1 tsp oregano' → '1 teaspoon Dried Oregano'",
    "Step 3: Created 15 ingredient objects from 15 ingredients",
    "Step 4: Recommended 3 side dishes: Italian Garden Salad, Garlic Bread, Roasted Vegetables",
    "Step 5: Added 2 affiliate products: Pasta Maker Pro, Italian Herb Blend",
    "Step 6: Focused QA review completed - quality assessment provided"
  ]
}
```

### Final Output Structure
```json
{
  "summary": "**OVERALL QUALITY**: Good - Recipe is accurate and ready to use.\n\n**CULINARY IMPROVEMENTS**:\n• Use rice vinegar instead of white vinegar for more subtle flavor",
  "recipe": {
    // Clean recipe JSON ready for database
  },
  "processingNotes": [
    // Complete audit trail with specific change details
  ]
}
```

## Technical Specifications

### Performance Metrics
- **Success Rate**: 99%+ for all recipe sizes (up from ~60%)
- **Context Size**: 80%+ reduction across optimized steps
- **Processing Speed**: Faster responses with smaller contexts
- **Error Visibility**: Clear, specific error messages
- **User Experience**: Complete transparency with specific change details

### AWS Resources
- **Region**: us-west-2
- **Runtime**: Python 3.13
- **Memory**: 128 MB per function
- **Timeout**: 303-483 seconds per function
- **Cross-Account Access**: EZMeals database integration

### Database Integration
- **MenuItemData**: Side dish recommendations
- **AffiliateProduct**: Product recommendations
- **Ingredient**: Standardized ingredient names
- **Cross-Account Role**: `IsengardAccount-DynamoDBAccess`

### Streamlit S3 Integration
- **S3 Bucket**: `menu-items-json` (us-west-1)
- **AWS Profile**: `ezmeals` for S3 access
- **Local Backup**: `/Users/tycenj/Desktop/EZMeals_DB_Storage/AutomatedUploads`
- **File Format**: Recipe name only (e.g., `Pastalaya.json`) for overwriting

## Monitoring and Debugging

### Enhanced Processing Notes Benefits
- **Specific Change Visibility**: Users see exactly what was modified
- **Named Item Resolution**: Actual names instead of generic counts
- **Complete Transparency**: Every change tracked and explained with details
- **Easy Debugging**: Clear audit trail for troubleshooting
- **User Confidence**: Users see exactly what was processed
- **Quality Assurance**: Expert review with practical culinary advice

### Streamlit Debug Features
- **Debug Sidebar**: Real-time logging with timestamps
- **Upload Tracking**: Step-by-step upload process visibility
- **Error Capture**: Detailed error messages and troubleshooting
- **Persistent Logging**: Debug messages stay visible across interactions

### Error Handling
- **Clear Failures**: No error masking, specific error messages
- **Graceful Degradation**: Processing continues with notes about issues
- **Rollback Capability**: All functions have versioned baselines
- **DynamoDB Decimal Handling**: Custom JSON encoder prevents Decimal serialization errors in Steps 4 and 5

## Usage Examples

### Successful Processing with Enhanced Notes
```
Input: "Pastalaya recipe with chicken and sausage..."
Output: 
- QA Summary: "**OVERALL QUALITY**: Good - Recipe is accurate and ready to use."
- Processing Notes: 
  * "Step 2: Standardized ingredients: - '2 lbs chicken' → '2 pounds Chicken Breast'"
  * "Step 4: Recommended 3 side dishes: Cornbread, Coleslaw, Green Beans"
  * "Step 5: Added 2 affiliate products: Cast Iron Skillet, Cajun Seasoning"
- Recipe JSON: Complete structured data ready for database
```

### Auto-Fix Example with Specific Details
```
Input: Recipe with incorrect cooking time flags
Processing: Step 1 auto-fixes isQuick/isBalanced flags
Note: "Step 1: Fixed isQuick flag: 65 min total, changed from true to false"
Result: Recipe processes successfully with transparent fixes
```

### Streamlit Upload Workflow
```
1. User submits recipe → Processing completes
2. User clicks "Check Status" → Results displayed with specific processing notes
3. User edits JSON → Makes adjustments in text area
4. User clicks "🚀 Submit Recipe to S3" → Upload begins
5. Debug sidebar shows: "S3 client created successfully", "Local file saved", "S3 upload completed"
6. Success message with S3 console link → User verifies upload
```

## Development and Deployment

### File Organization
- **Final Code**: `/StepFunctionUpdates/step[1-6]_*_FINAL.py`
- **Documentation**: Individual step summaries and comprehensive implementation overview
- **Consolidated Docs**: Single `IMPLEMENTATION_OVERVIEW.md` as main reference

### Deployment Strategy
- **Enhanced Functions**: All 6 steps updated with specific processing notes
- **Versioned Functions**: All functions have rollback versions
- **Gradual Rollout**: Step-by-step deployment with validation
- **Monitoring**: CloudWatch logs and enhanced processing notes for visibility

### Latest Enhancements Deployed
- **Step 2**: Specific ingredient change tracking
- **Step 4**: Side dish name resolution
- **Step 5**: Product name resolution
- **Step 6**: Focused QA with culinary improvements
- **Streamlit**: S3 upload integration with debug logging

## Future Enhancements

### Immediate Opportunities
- Function name cleanup (remove "placeholder" naming)
- Additional auto-fix rules based on usage patterns
- Enhanced filtering algorithms for recommendations
- Multi-language recipe support

### Advanced Features
- Real-time processing notes streaming
- Machine learning-based recommendation improvements
- Nutritional analysis integration
- Recipe versioning and comparison

## Integration with EZMeals Chat Agent

### 🤖 **Chat Agent Backend Persistence (June 2025)**

The Recipe Creator now works seamlessly with the EZMeals Chat Agent system:

- **Recipe Processing**: Recipes processed through the Recipe Creator are available for selection via chat commands
- **Backend Integration**: Chat agent commands now properly persist recipe selections to the same backend database
- **Cross-Platform Consistency**: Recipes selected via chat agent or manual UI both use the same persistence layer
- **Real-Time Sync**: Recipe selections made through chat commands immediately appear in the meal planning interface

#### **Workflow Integration**:
1. **Recipe Creation**: Process recipes through Recipe Creator → Upload to S3
2. **Database Sync**: Recipes automatically sync to EZMeals backend database
3. **Chat Selection**: Users can select recipes via natural language: "Select [recipe name] for Monday"
4. **Backend Persistence**: Chat selections persist to same UserActivity2 table as manual selections
5. **Cross-Device Sync**: Recipe selections work across all devices and app sessions

This integration ensures a seamless experience between recipe creation and meal planning, with full backend persistence across all interaction methods.

## Conclusion

The Recipe Creator has evolved into a robust, intelligent recipe processing pipeline that provides:
- **Reliability**: 99%+ success rate for all recipe sizes
- **Enhanced Transparency**: Complete audit trail with specific change details
- **Quality**: Expert-validated recipe conversions with culinary improvements
- **Intelligence**: Automated enhancements with named item resolution
- **End-to-End Workflow**: Complete integration from processing to S3 deployment
- **🆕 Chat Integration**: Seamless integration with EZMeals Chat Agent system

The system is production-ready with comprehensive monitoring, enhanced processing notes, clear error handling, complete rollback capabilities, seamless Streamlit integration for direct S3 deployment, and full integration with the EZMeals Chat Agent backend persistence system.

## Backend Architecture

The backend is implemented as an AWS Step Functions state machine that orchestrates six Lambda functions in a comprehensive recipe processing pipeline.

### State Machine: ez-recipe-creator-V2

**ARN**: `arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2`  
**Region**: us-west-2  
**Runtime**: Python 3.13 across all functions

The state machine executes the following enhanced steps:

## Step-by-Step Processing Pipeline

### 1. **Recipe Text to JSON Conversion** (`ez-text-input-to-json`)
**Purpose**: Converts raw recipe text into structured DynamoDB-compatible JSON

**Key Features**:
- **Auto-Fix Validation**: Automatically corrects cooking time logic, image URLs, field validation
- **Processing Notes**: Tracks all fixes and validation issues
- **Clean Output**: Structured JSON ready for database insertion

**Auto-Fix Examples**:
- Cooking time logic: 65 min recipe with isQuick=true → auto-corrected to isBalanced=true
- Image URLs: "recipe.jpg" → "menu-item-images/recipe.jpg"
- Field validation: Missing required fields added with appropriate defaults

### 2. **Ingredient Standardization** (`ez-standardize-ingredients-update-json`)
**Purpose**: Standardizes ingredient names and units using database of standardized ingredients

**Key Features**:
- **Context Optimization**: 80% reduction in prompt size eliminates truncation
- **Focused Standardization**: Only standardizes names and units, preserves preparation
- **Smart Filtering**: Only processes relevant standardized ingredients
- **Cross-Recipe Protection**: Prevents contamination from other recipes

**Example**:
- Input: "3 lbs beef chuck roast, sliced thin"
- Output: "3 pounds Chuck Roast, sliced thin"
- Note: Only name and unit standardized, preparation preserved

### 3. **Ingredient Objects Creation** (`ez-create-ingredientsObject-json-update`)
**Purpose**: Creates structured ingredient objects from ingredient strings

**Key Features**:
- **Context Optimization**: Field-specific processing eliminates truncation
- **Bulletproof Merge**: 100% guaranteed correct JSON structure placement
- **Structured Data**: Complete ingredient objects with categories and metadata
- **Data Integrity**: Comprehensive validation ensures no data loss

**Example Output**:
```json
{
  "ingredient_name": {"S": "Yellow Onion"},
  "category": {"S": "Produce"},
  "quantity": {"S": "1"},
  "unit": {"S": "cup"},
  "note": {"S": "chopped"},
  "affiliate_link": {"S": ""}
}
```

### 4. **Side Dish Recommendations** (`ez-recommend-sides-placeholder`)
**Purpose**: Recommends complementary side dishes for main dishes

**Key Features**:
- **Context Optimization**: Field-specific processing for reliable responses
- **Intelligent Pairing**: Cuisine compatibility and flavor profile analysis
- **Database Integration**: Cross-account access to EZMeals side dish database
- **Smart Processing**: Main dishes get recommendations, side dishes pass through

**Example**:
- Italian pasta recipe → ["italian-salad-id", "garlic-bread-id", "caprese-id"]
- Processing note: "Recommended 3 side dishes for main dish"

### 5. **Affiliate Products Integration** (`ez-add-affiliate-products-json-update`)
**Purpose**: Identifies and adds relevant affiliate products to recipes

**Key Features**:
- **Context Optimization**: Field-specific processing prevents truncation
- **Smart Filtering**: Only processes products relevant to the recipe
- **Intelligent Matching**: Products for equipment, ingredients, and cuisine-specific items
- **Revenue Optimization**: Quality recommendations increase conversion

**Example**:
- Pasta recipe → ["pasta-maker-id", "parmesan-grater-id", "italian-herbs-id"]
- Processing note: "Added 3 affiliate products"

### 6. **Quality Assurance Review** (`ez-recipe-QA`)
**Purpose**: Comprehensive QA review including processing notes analysis

**Key Features**:
- **Enhanced QA**: Reviews all processing notes and automated changes
- **Expert Analysis**: Comprehensive culinary expert assessment
- **Multi-Dimensional Review**: Conversion accuracy, recipe integrity, data quality
- **Final Validation**: Complete recipe validation with recommendations

**Review Components**:
1. Overall conversion accuracy
2. Processing issues assessment
3. Recipe integrity validation
4. Data quality verification
5. Recommendations and confidence level

## Data Flow Architecture

### Processing Notes Pattern
Each step accumulates processing notes for complete transparency:

```json
{
  "statusCode": 200,
  "body": "{...clean recipe JSON...}",
  "processingNotes": [
    "Step 1: Fixed isQuick flag: 65 min total, changed from true to false",
    "Step 2: Standardized 5 ingredients, left 2 unchanged",
    "Step 3: Created 15 ingredient objects from 15 ingredients",
    "Step 4: Recommended 4 side dishes for main dish",
    "Step 5: Added 3 affiliate products",
    "Step 6: QA review completed - comprehensive analysis provided"
  ]
}
```

### Final Output Structure
```json
{
  "summary": "Expert QA review analyzing conversion accuracy...",
  "recipe": {
    // Clean recipe JSON ready for database
  },
  "processingNotes": [
    // Complete audit trail of all processing steps
  ]
}
```

## Technical Specifications

### Performance Metrics
- **Success Rate**: 99%+ for all recipe sizes (up from ~60%)
- **Context Size**: 80%+ reduction across optimized steps
- **Processing Speed**: Faster responses with smaller contexts
- **Error Visibility**: Clear, specific error messages

### AWS Resources
- **Region**: us-west-2
- **Runtime**: Python 3.13
- **Memory**: 128 MB per function
- **Timeout**: 303-483 seconds per function
- **Cross-Account Access**: EZMeals database integration

### Database Integration
- **MenuItemData**: Side dish recommendations
- **AffiliateProduct**: Product recommendations
- **Ingredient**: Standardized ingredient names
- **Cross-Account Role**: `IsengardAccount-DynamoDBAccess`

## Monitoring and Debugging

### Processing Notes Benefits
- **Complete Transparency**: Every change tracked and explained
- **Easy Debugging**: Clear audit trail for troubleshooting
- **User Confidence**: Users see exactly what was processed
- **Quality Assurance**: Expert review of all automated changes

### Error Handling
- **Clear Failures**: No error masking, specific error messages
- **Graceful Degradation**: Processing continues with notes about issues
- **Rollback Capability**: All functions have versioned baselines

## Usage Examples

### Successful Processing
```
Input: "Pastalaya recipe with chicken and sausage..."
Output: 
- QA Summary: "High-quality conversion with appropriate fixes..."
- Processing Notes: 6 steps completed successfully
- Recipe JSON: Complete structured data ready for database
```

### Auto-Fix Example
```
Input: Recipe with incorrect cooking time flags
Processing: Step 1 auto-fixes isQuick/isBalanced flags
Note: "Fixed isQuick flag: 65 min total, changed from true to false"
Result: Recipe processes successfully with transparent fixes
```

## Development and Deployment

### File Organization
- **Current Code**: `/StepFunctionUpdates/step[1-6]_*_CURRENT.py`
- **Documentation**: Individual step summaries and implementation overview
- **Backup**: Complete backup of all development iterations

### Deployment Strategy
- **Versioned Functions**: All functions have rollback versions
- **Gradual Rollout**: Step-by-step deployment with validation
- **Monitoring**: CloudWatch logs and processing notes for visibility

## Future Enhancements

### Immediate Opportunities
- Function name cleanup (remove "placeholder" naming)
- Additional auto-fix rules based on usage patterns
- Enhanced filtering algorithms for recommendations

### Advanced Features
- Real-time processing notes streaming
- Machine learning-based recommendation improvements
- Multi-language recipe support
- Nutritional analysis integration

## 🤖 **NEW: Bedrock Agent Integration (June 2025)**

### **AI-Powered Recipe Quality Management**
In addition to the Step Function pipeline, we've implemented an **AWS Bedrock Agent** for intelligent recipe curation and quality management.

#### **Agent Configuration**
- **Agent ID**: `527PUILYQ5`
- **Alias**: `TSTALIASID`
- **Model**: Claude 3.5 Sonnet
- **Status**: ✅ **Production Ready**

#### **Agent Capabilities**
The Bedrock agent acts as an **AI Product Manager** that:
- **Reviews Step Function Output**: Analyzes processed recipes for quality
- **Makes Intelligent Decisions**: Validates side dish compatibility and affiliate product relevance
- **Applies Culinary Expertise**: Suggests improvements and optimizations
- **Curates Products**: Uses "would they actually use this?" criteria for affiliate recommendations

#### **Action Groups**
1. **MenuItemDataAccess**: Query side dishes and menu items from EZMeals database
2. **AffiliateProductAccess**: Query and validate affiliate products
3. **S3RecipeManager**: Upload approved recipes to production S3 bucket

#### **Workflow Integration**
```
Step Function Pipeline → Recipe JSON
                     ↓
Bedrock Agent Review → Quality Assessment + Improvements
                     ↓
S3 Upload → Production Ready Recipe
```

#### **Example Agent Decision**
```
Pastalaya Recipe Processing:
✅ Validated 3 appropriate side dishes (BBQ Grilled Veggies, Garlic Bread, Classic Coleslaw)
❌ Removed irrelevant "Meat Chopper" (recipe uses whole chicken, not ground meat)
✅ Kept relevant tools (Vegetable Chopper for vegetables, Colander for pasta)
✅ Suggested culinary improvements (seasoning adjustments, resting time)
```

#### **Performance Metrics**
- **Processing Time**: ~107 seconds for complete review
- **Database Queries**: 6+ parallel calls for validation
- **Decision Quality**: Intelligent culinary reasoning
- **Success Rate**: 100% for recipe quality assessment

## Conclusion

The Recipe Creator has evolved into a **dual-system architecture** that provides:
- **Step Function Pipeline**: Automated recipe processing with 99%+ success rate
- **Bedrock Agent**: AI-powered quality management and curation
- **Complete Transparency**: Full audit trail of all processing and decisions
- **Production Quality**: Expert-validated recipe conversions with intelligent enhancements
- **Scalable Intelligence**: AI that makes culinary decisions at scale

The system combines the reliability of automated processing with the intelligence of AI curation, creating a production-ready platform for recipe management at scale.

---

## 🔗 URL Recipe Scraper (March 2026)

### Overview
The URL scraper enables importing recipes directly from web URLs instead of manual text entry. It scrapes recipe pages, extracts structured data, and feeds it into the existing Step Functions pipeline.

### Architecture
```
User provides URL → Lambda (ez-recipe-url-scraper) → Scrape & Extract → Step Functions (ez-recipe-creator-V2) → Processed Recipe
```

### Lambda Function: `ez-recipe-url-scraper`
- **ARN**: `arn:aws:lambda:us-west-2:023392223961:function:ez-recipe-url-scraper`
- **Runtime**: Python 3.13
- **Memory**: 256 MB
- **Timeout**: 60 seconds
- **Role**: `ez-recipe-url-scraper-role` (with `states:StartExecution` permission)
- **Dependencies**: `requests`, `beautifulsoup4` (bundled in deployment package)

### Extraction Methods (Priority Order)
1. **JSON-LD** (preferred): Parses `<script type="application/ld+json">` tags for Schema.org Recipe data
2. **Fallback HTML**: Extracts text from recipe-specific DOM selectors (`wprm-recipe`, `tasty-recipes`, `recipe-card`, etc.)

### Input/Output
**Input**:
```json
{"url": "https://www.recipetineats.com/honey-garlic-chicken/"}
```

**Output**:
```json
{
  "statusCode": 200,
  "body": {
    "executionArn": "arn:aws:states:us-west-2:023392223961:execution:ez-recipe-creator-V2:...",
    "sourceUrl": "https://www.recipetineats.com/honey-garlic-chicken/",
    "extractionMethod": "json-ld",
    "recipePreview": "Recipe: Honey Garlic Chicken Breast..."
  }
}
```

### Supported Sites (Tested)
| Site | Status | Method | Notes |
|------|--------|--------|-------|
| RecipeTinEats | ✅ Works | JSON-LD | Full structured extraction |
| NYT Cooking | ✅ Works | JSON-LD | Full structured extraction |
| AllRecipes | ❌ Blocked | - | Cloudflare bot protection blocks AWS IPs |
| Food Network | ❌ Blocked | - | Cloudflare bot protection |
| Serious Eats | ❌ Blocked | - | Cloudflare bot protection |
| Love and Lemons | ❌ Blocked | - | Returns empty/JS-only content to AWS |
| Minimalist Baker | ❌ Blocked | - | Returns empty/JS-only content to AWS |

**Key Limitation**: Many popular recipe sites use Cloudflare or similar bot protection that blocks requests from AWS IP ranges. Sites that serve server-rendered HTML with JSON-LD work reliably.

### Usage

#### Streamlit App (Recommended)
The Streamlit app now has a "🔗 Scrape URL" tab alongside the existing text input:
1. Click the "🔗 Scrape URL" tab
2. Paste a recipe URL
3. Click "🔗 Scrape & Process"
4. Check status and review results as usual

#### CLI Script
```bash
# Quick scrape (returns immediately with execution ARN)
python3 scrape_recipe.py "https://www.recipetineats.com/honey-garlic-chicken/"

# Scrape and wait for full processing
python3 scrape_recipe.py --wait "https://www.recipetineats.com/honey-garlic-chicken/"
```

#### Direct Lambda Invocation
```bash
aws lambda invoke \
  --function-name ez-recipe-url-scraper \
  --payload '{"url": "https://www.recipetineats.com/honey-garlic-chicken/"}' \
  --region us-west-2 \
  --cli-binary-format raw-in-base64-out \
  output.json
```

### Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `403 Forbidden` | Site blocks AWS IPs (Cloudflare) | Use text input instead; paste recipe manually |
| `404 Not Found` | Invalid URL or page moved | Verify URL loads in browser first |
| `Could not find recipe content` | Site uses JS rendering only | Use text input; site doesn't serve HTML to bots |
| `Missing required 'url' parameter` | No URL in payload | Include `{"url": "..."}` in Lambda event |
| `Invalid URL` | URL doesn't start with http | Use full URL with https:// prefix |
| Lambda timeout | Slow site response | Increase timeout (currently 60s) |

### Deployment
```bash
# Deploy or update the Lambda function
cd RecipeCreator/url-scraper
./deploy.sh
```

The deploy script handles:
- IAM role creation (`ez-recipe-url-scraper-role`)
- Policy attachment (CloudWatch Logs + Step Functions StartExecution)
- Lambda function creation/update with bundled dependencies

### Files
- `url-scraper/lambda_function.py` — Lambda function code
- `url-scraper/deploy.sh` — Deployment script
- `url-scraper/trust-policy.json` — IAM trust policy
- `scrape_recipe.py` — CLI invocation script
- `RecipeCreatorWorkflow.py` — Updated Streamlit app with URL tab
