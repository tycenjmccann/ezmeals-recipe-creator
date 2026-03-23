# New Step Added: Recommend Side Dishes

## Overview
Added a new step to the Recipe Creator state machine between "Create ingredient_objects" and "Identify Affiliate Products" to recommend side dishes for main dishes.

## Implementation Summary

### ✅ **Placeholder Lambda Function Created**
- **Function Name**: `ez-recommend-sides-placeholder`
- **ARN**: `arn:aws:lambda:us-west-2:023392223961:function:ez-recommend-sides-placeholder`
- **Runtime**: Python 3.13
- **Role**: `arn:aws:iam::023392223961:role/service-role/AppendTextFunction-role-75ontcl2` (same as other functions)
- **Timeout**: 303 seconds
- **Memory**: 128 MB
- **Status**: Active

### ✅ **State Machine Updated**
- **State Machine**: `ez-recipe-creator-V2`
- **New Step Position**: Between "Create ingredient_objects" and "Identify Affiliate Products"
- **Step Name**: "Recommend Side Dishes"
- **Update Date**: 2025-06-18T22:58:12.859000-07:00
- **Revision ID**: 1f868408-ccee-4e3f-870d-b176c0d84806

## Updated State Machine Flow

```
1. Create json from Text
2. Standardize Ingredients  
3. Create ingredient_objects
4. Recommend Side Dishes ← NEW STEP
5. Identify Affiliate Products
6. Recipe QA
```

## Data Flow Pattern

The new step follows the established pattern:
- **InputPath**: `"$"` (receives full context)
- **ResultPath**: `"$.stepOutput"` (outputs to stepOutput)
- **Input**: Complete event including previous stepOutput
- **Output**: Updated JSON with recommendedSides populated

## Placeholder Function Behavior

### Current Implementation (Hello World):
1. **Extracts recipe data** from `event.stepOutput.body`
2. **Checks dish type** using `dishType.S` field
3. **Logs appropriate message**:
   - Main dish: "Hello World - Processing main dish for side recommendations!"
   - Side dish: "Hello World - Side dish detected, passing through unchanged"
4. **Passes data through unchanged** (placeholder behavior)

### Future Implementation Plan:
1. **Check if main dish** (`dishType == "main"`)
2. **Query menu database** for available side dishes
3. **Use LLM (Claude)** to analyze main dish and recommend compatible sides
4. **Update recommendedSides field** with side dish IDs
5. **Pass through unchanged** for side dishes

## Testing the New Step

### Test with Streamlit App:
1. Submit a main dish recipe through RecipeCreatorWorkflow.py
2. Check CloudWatch logs for the new function
3. Look for "Hello World" messages indicating the step is working
4. Verify data flows correctly to subsequent steps

### Expected Log Messages:
- **Main Dish**: `"Hello World - Processing main dish for side recommendations!"`
- **Side Dish**: `"Hello World - Side dish detected, passing through unchanged"`

## Files Created

1. **`ez-recommend-sides-placeholder.py`** - Placeholder Lambda function code
2. **`updated_state_machine.json`** - New state machine definition
3. **`new_step_summary.md`** - This documentation

## Next Steps

### Phase 1: Verify Placeholder (Current)
- [ ] Test state machine with main dish recipe
- [ ] Test state machine with side dish recipe  
- [ ] Verify logs show correct "Hello World" messages
- [ ] Confirm data flows to subsequent steps

### Phase 2: Implement Full Functionality
- [ ] Add cross-account DynamoDB access for menu items
- [ ] Implement Claude integration for side recommendations
- [ ] Add logic to populate `recommendedSides` field
- [ ] Add error handling and retry logic
- [ ] Test with various main dish types

### Phase 3: Optimization
- [ ] Cache menu items for performance
- [ ] Add cuisine-specific side recommendations
- [ ] Implement dietary restriction matching
- [ ] Add logging and metrics

## State Machine Definition Changes

### Before:
```json
"Create ingredient_objects": {
  "Next": "Identify Affiliate Products"
}
```

### After:
```json
"Create ingredient_objects": {
  "Next": "Recommend Side Dishes"
},
"Recommend Side Dishes": {
  "Type": "Task",
  "Resource": "arn:aws:lambda:us-west-2:023392223961:function:ez-recommend-sides-placeholder:$LATEST",
  "InputPath": "$",
  "ResultPath": "$.stepOutput",
  "Next": "Identify Affiliate Products"
}
```

## Rollback Plan

If issues arise with the new step:

1. **Remove the step** from state machine:
   ```json
   "Create ingredient_objects": {
     "Next": "Identify Affiliate Products"  // Direct connection
   }
   ```

2. **Update state machine** to skip the new step

3. **Delete Lambda function** if needed:
   ```bash
   aws lambda delete-function --function-name ez-recommend-sides-placeholder
   ```

## Success Criteria

- ✅ State machine executes without errors
- ✅ New step processes main dishes (logs "Hello World" message)
- ✅ New step skips side dishes appropriately  
- ✅ Data flows correctly to subsequent steps
- ✅ Final output maintains all existing functionality

The placeholder is now ready for testing and future implementation of the full side recommendation logic!
