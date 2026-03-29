#!/usr/bin/env python3
"""
Batch runner for EZmeals Recipe Creator (Strands Agents pipeline)
Processes all 21 expansion recipes and saves results for A/B comparison.
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime

# Environment setup
os.makedirs('/home/ssm-user/.openclaw/workspace/.strands/workflows', exist_ok=True)
os.environ['STRANDS_WORKFLOW_DIR'] = '/home/ssm-user/.openclaw/workspace/.strands/workflows'
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-west-2')

# Add pip install path
sys.path.insert(0, '/tmp/pip-install')

# Import after env setup
from recipe_graph import orchestrator

# All 21 recipe URLs from the expansion batch
RECIPES = [
    {"name": "Thai Grilled Chicken (Gai Yang)", "url": "https://www.simplysuwanee.com/gai-yang/"},
    {"name": "Red Braised Pork Belly", "url": "https://thewoksoflife.com/red-braised-pork-belly-mao/"},
    {"name": "Chinese Braised Beef Shank", "url": "https://thewoksoflife.com/chinese-braised-beef-shank/"},
    {"name": "Soy Sauce Chicken", "url": "https://thewoksoflife.com/soy-sauce-chicken/"},
    {"name": "Korean Beef Bulgogi Bowls", "url": "https://thewoksoflife.com/bulgogi/"},
    {"name": "Khao Soi Curry Noodles", "url": "https://www.simplysuwanee.com/khao-soi/"},
    {"name": "Slow Cooker Chicken Tikka Masala", "url": "https://tastesbetterfromscratch.com/slow-cooker-chicken-tikka-masala/"},
    {"name": "Slow Cooker Coconut Curry Lentils", "url": "https://www.budgetbytes.com/slow-cooker-coconut-curry-lentils/"},
    {"name": "Chickpea Curry", "url": "https://tastesbetterfromscratch.com/chickpea-curry/"},
    {"name": "Chicken Tikka Masala From Scratch", "url": "https://tastesbetterfromscratch.com/chicken-tikka-masala-recipe/"},
    {"name": "Red Lentil Curry", "url": "https://rainbowplantlife.com/vegan-red-lentil-curry/"},
    {"name": "Baked Ziti", "url": "https://www.twopeasandtheirpod.com/baked-ziti/"},
    {"name": "Stuffed Shells", "url": "https://www.twopeasandtheirpod.com/stuffed-shells/"},
    {"name": "Short Rib Ragu", "url": "https://tastesbetterfromscratch.com/short-rib-ragu/"},
    {"name": "Italian Wedding Soup", "url": "https://www.budgetbytes.com/italian-wedding-soup/"},
    {"name": "Chicken and Dumplings", "url": "https://tastesbetterfromscratch.com/chicken-and-dumplings/"},
    {"name": "Tom Kha Gai (Coconut Chicken Soup)", "url": "https://www.simplysuwanee.com/tom-kha-gai/"},
    {"name": "Hearty Beef Stew", "url": "https://tastesbetterfromscratch.com/beef-stew/"},
    {"name": "French Onion Soup", "url": "https://tastesbetterfromscratch.com/french-onion-soup/"},
    {"name": "Slow Cooker White Chicken Chili", "url": "https://www.budgetbytes.com/slow-cooker-white-chicken-chili/"},
    {"name": "Turkey Sweet Potato Chili", "url": "https://www.twopeasandtheirpod.com/turkey-sweet-potato-chili/"},
]

OUTPUT_DIR = "/home/ssm-user/.openclaw/workspace/ezmeals-recipe-creator/batch-output"
STATE_FILE = os.path.join(OUTPUT_DIR, "batch_state.json")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "started_at": datetime.now().isoformat()}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def run_recipe(name, url, state):
    """Run a single recipe through the pipeline."""
    # Skip if already completed
    if url in state["completed"]:
        print(f"⏭️  {name} — already completed, skipping")
        return True

    print(f"\n{'='*80}")
    print(f"🔗 Processing: {name}")
    print(f"   URL: {url}")
    print(f"{'='*80}")

    start = time.time()
    try:
        result = orchestrator(f"Process this recipe URL: {url}")
        elapsed = time.time() - start

        # Save result
        result_file = os.path.join(OUTPUT_DIR, f"{name.replace(' ', '_').replace('/', '_')}_result.txt")
        with open(result_file, 'w') as f:
            f.write(f"Recipe: {name}\nURL: {url}\nTime: {elapsed:.1f}s\n\n{str(result)}")

        state["completed"].append(url)
        save_state(state)
        print(f"✅ {name} — completed in {elapsed:.1f}s")
        return True

    except Exception as e:
        elapsed = time.time() - start
        error_msg = traceback.format_exc()
        print(f"❌ {name} — FAILED after {elapsed:.1f}s: {e}")

        # Save error
        error_file = os.path.join(OUTPUT_DIR, f"{name.replace(' ', '_').replace('/', '_')}_error.txt")
        with open(error_file, 'w') as f:
            f.write(f"Recipe: {name}\nURL: {url}\nTime: {elapsed:.1f}s\nError: {error_msg}")

        state["failed"].append({"name": name, "url": url, "error": str(e)})
        save_state(state)
        return False


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    state = load_state()

    # Allow starting from a specific index
    start_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    print(f"🚀 EZmeals Batch Pipeline — {len(RECIPES)} recipes")
    print(f"   Previously completed: {len(state['completed'])}")
    print(f"   Starting from index: {start_idx}")
    print()

    success = 0
    fail = 0

    for i, recipe in enumerate(RECIPES):
        if i < start_idx:
            continue
        ok = run_recipe(recipe["name"], recipe["url"], state)
        if ok:
            success += 1
        else:
            fail += 1

    print(f"\n{'='*80}")
    print(f"🏁 BATCH COMPLETE: {success} succeeded, {fail} failed out of {len(RECIPES)}")
    print(f"   Output: {OUTPUT_DIR}")
    state["finished_at"] = datetime.now().isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
