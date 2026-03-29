"""
EZmeals Batch Recipe Runner v2
Runs recipe_graph_v2 pipeline sequentially on a list of URLs.
Falls back to manual publish if the graph's publish node fails.

Usage:
    python run_batch_v2.py                    # Run all pending
    python run_batch_v2.py --start 5          # Start from recipe #5
    python run_batch_v2.py --limit 3          # Only run 3 recipes
"""
import sys, os, json, time, re, asyncio, subprocess, shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Ensure strands workflow dir is set
os.environ.setdefault('STRANDS_WORKFLOW_DIR', '/home/ssm-user/.openclaw/workspace/.strands/workflows')

# Import publish tool for fallback
from strands_agents_recipe_creator import publish_recipe

# ============================================================================
# BATCH 1 RECIPES
# ============================================================================
BATCH_1 = [
    # ASIAN
    ("Pad Thai", "https://tastesbetterfromscratch.com/pad-thai/"),
    ("Thai Basil Chicken", "https://tastesbetterfromscratch.com/thai-basil-chicken/"),  # DONE
    ("Pho", "https://tastesbetterfromscratch.com/pho-noodle-soup/"),
    ("Orange Chicken", "https://tastesbetterfromscratch.com/orange-peel-chicken/"),
    ("Teriyaki Chicken Kebabs", "https://tastesbetterfromscratch.com/teriyaki-chicken-kebabs/"),
    ("Mongolian Noodles", "https://tastesbetterfromscratch.com/mongolian-noodles/"),
    ("Veggie Lo Mein", "https://www.skinnytaste.com/20-minute-veggie-lo-mein-bowl/"),
    ("Kung Pao Chicken Zoodles", "https://www.skinnytaste.com/kung-pao-chicken-zoodles-for-two/"),
    ("Sesame Noodles", "https://www.healthygffamily.com/recipe/takeout-style-sesame-noodles/"),
    # INDIAN
    ("Chickpea Curry", "https://tastesbetterfromscratch.com/chickpea-curry/"),
    ("Yellow Curry", "https://tastesbetterfromscratch.com/yellow-curry/"),
    ("Chicken Coconut Curry", "https://www.skinnytaste.com/chicken-curry-with-coconut-milk-43-pts/"),
    # GLOBAL
    ("Chickpea Shawarma Bowls", "https://www.twopeasandtheirpod.com/chickpea-shawarma-bowls/"),
    ("Mediterranean Salmon", "https://www.skinnytaste.com/mediterranean-salmon-sheet-pan-dinner/"),
    ("Vietnamese Shaking Beef", "https://www.skinnytaste.com/vietnamese-shaking-beef-bo-luc-lac/"),
    ("Mediterranean Falafel Bowl", "https://www.healthygffamily.com/recipe/mediterranean-falafel-bowl/"),
    # SOUPS & STEWS
    ("Lemon Chicken Orzo Soup", "https://tastesbetterfromscratch.com/lemon-chicken-orzo-soup/"),
    ("Cabbage Roll Soup", "https://tastesbetterfromscratch.com/cabbage-roll-soup/"),
    ("Turkey Chili", "https://tastesbetterfromscratch.com/turkey-chili-recipe/"),
    ("Slow Cooker Beef Stroganoff", "https://www.skinnytaste.com/slow-cooker-beef-stroganoff/"),
]

# Track completed recipes
STATUS_FILE = os.path.join(SCRIPT_DIR, 'batch_status.json')

def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "skipped": []}

def save_status(status):
    with open(STATUS_FILE, 'w') as f:
        json.dump(status, f, indent=2)

def slug_from_name(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

def try_manual_publish(recipe_name):
    """Attempt to manually publish from node output files if pipeline publish failed."""
    from schema_validator import convert_pipeline_to_schema, validate_recipe_schema, side_by_side_comparison
    
    qa_file = os.path.join(SCRIPT_DIR, 'output_node_qa_review.txt')
    if not os.path.exists(qa_file):
        return False, "No QA output file found"
    
    with open(qa_file) as f:
        qa_text = f.read()
    
    # Extract JSON from QA output
    match = re.search(r'```json\s*\n(.*?)\n```', qa_text, re.DOTALL)
    if not match:
        return False, "No JSON found in QA output"
    
    try:
        recipe_raw = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    
    # Convert to correct schema format
    slug = slug_from_name(recipe_name)
    recipe = convert_pipeline_to_schema(recipe_raw)
    
    # Inject enricher IDs (products + recommendedSides) from saved JSON
    enricher_file = os.path.join(SCRIPT_DIR, 'enricher_recommendations.json')
    if os.path.exists(enricher_file):
        import json as _json_enrich
        with open(enricher_file) as ef:
            enrich_data = _json_enrich.load(ef)
        
        if enrich_data.get('recommendedSides') and not recipe.get('recommendedSides'):
            recipe['recommendedSides'] = enrich_data['recommendedSides']
            print(f"  ✅ Injected {len(enrich_data['recommendedSides'])} side IDs from enricher")
        
        if enrich_data.get('products') and not recipe.get('products'):
            recipe['products'] = enrich_data['products']
            print(f"  ✅ Injected {len(enrich_data['products'])} product IDs from enricher")
    
    # Inject search terms from node output
    search_file = os.path.join(SCRIPT_DIR, 'output_node_search_terms.txt')
    if os.path.exists(search_file) and not recipe.get('searchTerms'):
        with open(search_file) as sf:
            search_text = sf.read()
        terms_match = _re.search(r'SEARCH_TERMS[:\s]*\[([^\]]+)\]', search_text)
        if terms_match:
            terms = _re.findall(r'["\']([^"\']+)["\']', terms_match.group(1))
            if terms:
                recipe['searchTerms'] = terms
                print(f"  ✅ Injected {len(terms)} search terms")
    
    # Validate against gold standard
    is_valid, errors, warnings = validate_recipe_schema(recipe)
    comparison = side_by_side_comparison(recipe)
    print(comparison)
    
    if not is_valid:
        error_report = "\n".join(f"  ❌ {e}" for e in errors)
        return False, f"Schema validation failed:\n{error_report}"
    
    print(f"✅ Schema validation passed ({len(warnings)} warnings)")
    
    # Find image files — search multiple patterns including glob for partial matches
    import glob
    name_under = recipe_name.replace(' ', '_')
    
    hero = None
    thumb = None
    
    # Direct pattern matches first
    hero_patterns = [
        f'/tmp/{name_under}-landscape',
        f'/tmp/{slug}-landscape',
    ]
    thumb_patterns = [
        f'/tmp/{name_under}-thumbnail',
        f'/tmp/{slug}-thumbnail',
    ]
    
    for pattern in hero_patterns:
        for ext in ['.png', '.jpg']:
            if os.path.exists(pattern + ext):
                hero = pattern + ext
                break
        if hero:
            break
    
    for pattern in thumb_patterns:
        for ext in ['.png', '.jpg']:
            if os.path.exists(pattern + ext):
                thumb = pattern + ext
                break
        if thumb:
            break
    
    # Glob fallback: find most recent landscape/thumbnail in /tmp/ matching any part of the name
    if not hero:
        # Try first two words of name as prefix
        prefix_words = name_under.split('_')[:2]
        for pw_count in [2, 1]:
            prefix = '_'.join(prefix_words[:pw_count])
            matches = sorted(glob.glob(f'/tmp/{prefix}*-landscape.*'), key=os.path.getmtime, reverse=True)
            if matches:
                hero = matches[0]
                break
    
    if not thumb:
        prefix_words = name_under.split('_')[:2]
        for pw_count in [2, 1]:
            prefix = '_'.join(prefix_words[:pw_count])
            matches = sorted(glob.glob(f'/tmp/{prefix}*-thumbnail.*'), key=os.path.getmtime, reverse=True)
            if matches:
                thumb = matches[0]
                break
    
    # Also check the recipe output dir for canonical hero.*/thumbnail.* files
    if not hero:
        for ext in ['.png', '.jpg']:
            candidate = os.path.join(SCRIPT_DIR, 'output', slug, f'hero{ext}')
            if os.path.exists(candidate):
                hero = candidate
                break
    
    if not thumb:
        for ext in ['.png', '.jpg']:
            candidate = os.path.join(SCRIPT_DIR, 'output', slug, f'thumbnail{ext}')
            if os.path.exists(candidate):
                thumb = candidate
                break
    
    # Publish even without images — JSON is more important than images
    # publish_recipe has its own fallback search and will handle missing images gracefully
    result = publish_recipe(
        recipe_json_str=json.dumps(recipe),
        hero_image_path=hero or "",
        thumbnail_image_path=thumb or "",
        recipe_id=slug
    )
    
    success = '✅ S3 JSON' in str(result)
    return success, str(result)


async def run_single(name, url):
    """Run the pipeline on a single recipe URL."""
    from recipe_graph_v2 import build_pipeline, log, LOG_FILE, RECIPE_ID
    import importlib
    import recipe_graph_v2
    
    # Reset the recipe ID for each run
    import uuid
    recipe_graph_v2.RECIPE_ID = str(uuid.uuid4()).lower()
    
    # Reload to get fresh prompts with new ID
    importlib.reload(recipe_graph_v2)
    
    open(LOG_FILE, 'w').close()
    
    print(f"\n{'='*60}")
    print(f"  PROCESSING: {name}")
    print(f"  URL: {url}")
    print(f"  ID: {recipe_graph_v2.RECIPE_ID}")
    print(f"{'='*60}\n")
    
    start = time.time()
    try:
        graph = recipe_graph_v2.build_pipeline()
        result = await graph.invoke_async(f"Process this recipe URL: {url}")
        elapsed = time.time() - start
        
        print(f"\n✅ Pipeline complete in {elapsed:.0f}s ({elapsed/60:.1f}m)")
        print(f"Status: {result.status}")
        
        # Save node outputs
        for node_id in ['scraper', 'chef_review', 'json_converter', 'ingredient_standardizer',
                         'ingredient_objects', 'enricher', 'search_terms', 'qa_review',
                         'image_gen', 'publish']:
            node_result = result.results.get(node_id)
            if node_result and node_result.result:
                msg = node_result.result.message
                if msg and 'content' in msg:
                    text = ''
                    for block in msg['content']:
                        if 'text' in block:
                            text = block['text']
                            break
                    output_path = os.path.join(SCRIPT_DIR, f'output_node_{node_id}.txt')
                    with open(output_path, 'w') as f:
                        f.write(text)
        
        # Check if publish succeeded
        publish_result = result.results.get('publish')
        publish_text = ''
        if publish_result and publish_result.result:
            msg = publish_result.result.message
            if msg and 'content' in msg:
                for block in msg['content']:
                    if 'text' in block:
                        publish_text = block['text']
                        break
        
        published = '✅ DynamoDB' in publish_text and '✅ S3' in publish_text
        
        if not published:
            print(f"⚠️  Pipeline publish node didn't complete — attempting manual publish...")
            success, pub_result = try_manual_publish(name)
            if success:
                print(f"✅ Manual publish succeeded!")
                print(pub_result)
                published = True
            else:
                print(f"❌ Manual publish also failed: {pub_result}")
        
        return published, elapsed
        
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n❌ Pipeline FAILED after {elapsed:.0f}s: {e}")
        import traceback
        traceback.print_exc()
        return False, elapsed


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args()
    
    status = load_status()
    completed_names = set(status['completed'])
    
    # Filter to pending recipes
    pending = [(i, name, url) for i, (name, url) in enumerate(BATCH_1) 
               if name not in completed_names]
    
    # Apply start/limit
    pending = pending[args.start:]
    if args.limit:
        pending = pending[:args.limit]
    
    print(f"\n🍳 EZ Meals Batch Runner v2")
    print(f"   Total recipes: {len(BATCH_1)}")
    print(f"   Already done: {len(completed_names)}")
    print(f"   Running now: {len(pending)}")
    print(f"   Skipped: {status.get('skipped', [])}")
    print(f"   Failed: {status.get('failed', [])}")
    
    for i, (idx, name, url) in enumerate(pending):
        print(f"\n{'#'*60}")
        print(f"  Recipe {i+1}/{len(pending)}: {name}")
        print(f"{'#'*60}")
        
        success, elapsed = await run_single(name, url)
        
        if success:
            status['completed'].append(name)
            print(f"\n✅ {name} — PUBLISHED ({elapsed:.0f}s)")
        else:
            status['failed'].append({"name": name, "url": url, "time": elapsed})
            print(f"\n❌ {name} — FAILED ({elapsed:.0f}s)")
        
        save_status(status)
        
        # Brief pause between recipes
        if i < len(pending) - 1:
            print(f"\n⏳ Cooling down 10s before next recipe...")
            await asyncio.sleep(10)
    
    # Final summary
    print(f"\n{'='*60}")
    print(f"  BATCH COMPLETE")
    print(f"{'='*60}")
    print(f"  ✅ Published: {len(status['completed'])}")
    print(f"  ❌ Failed: {len(status['failed'])}")
    for f in status.get('failed', []):
        print(f"     - {f['name']}: {f.get('url', 'unknown')}")

if __name__ == "__main__":
    asyncio.run(main())
