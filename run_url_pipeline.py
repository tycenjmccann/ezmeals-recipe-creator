"""Run the full pipeline from a URL."""

from strands import Agent, tool
from strands_tools import http_request, journal
import json
import sys
import uuid
import re
import os
import requests
from bs4 import BeautifulSoup

# Import our agents and tools
from strands_agents_recipe_creator import (
    text_to_json_agent, qa_agent, url_scraper_agent,
    scrape_recipe_url, validate_recipe_json, generate_recipe_images
)

def extract_json_from_response(text):
    if '```json' in text:
        start = text.index('```json') + 7
        end = text.index('```', start)
        return text[start:end].strip()
    elif '```' in text:
        start = text.index('```') + 3
        end = text.index('```', start)
        return text[start:end].strip()
    return text.strip()

def get_message_text(result):
    if isinstance(result.message, dict):
        return result.message['content'][0]['text']
    return str(result.message)

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.iankewks.com/thai-crying-tiger-steak/"
recipe_id = str(uuid.uuid4())

print(f"🔗 FULL PIPELINE TEST")
print(f"URL: {url}")
print(f"Recipe ID: {recipe_id}")
print("=" * 80)

# Step 0: Scrape URL
print(f"\n🤖 AGENT 0: URL Scraper...")
sys.stdout.flush()
scrape_result = url_scraper_agent(f"Scrape this recipe URL: {url}")
scrape_text = get_message_text(scrape_result)
print(scrape_text[:500])

# Extract just the recipe text portion for the next agent
recipe_start = scrape_text.find("RECIPE TEXT:")
if recipe_start >= 0:
    recipe_text = scrape_text[recipe_start + len("RECIPE TEXT:"):].strip()
else:
    recipe_text = scrape_text

# Extract image path
image_path = None
img_match = re.search(r'Image saved: (.+?)(?:\n|$)', scrape_text)
if img_match:
    image_path = img_match.group(1).strip()
    print(f"\n📸 Image downloaded: {image_path}")

print(f"\n{'='*80}")

# Step 1: Text to JSON
print(f"\n🤖 AGENT 1: Text to JSON (with validation tool)...")
sys.stdout.flush()
step1_result = text_to_json_agent(f"Recipe Details:\n{recipe_text}\n\nUse recipe ID: {recipe_id}")
step1_text = get_message_text(step1_result)

# Extract the JSON
json_str = extract_json_from_response(step1_text)
try:
    processed = json.loads(json_str)
    title = processed.get('title', {}).get('S', 'Unknown')
    instructions = []
    for item in processed.get('instructions', {}).get('L', []):
        if 'S' in item:
            instructions.append(item['S'])
        elif 'M' in item:
            instructions.append(item['M'].get('step', {}).get('S', str(item)))
    ingredients = []
    for item in processed.get('ingredients', {}).get('L', []):
        if 'S' in item:
            ingredients.append(item['S'])
        elif 'M' in item:
            ingredients.append(item['M'].get('name', {}).get('S', str(item)))
    print(f"\n✅ Recipe: {title}")
    print(f"   {len(ingredients)} ingredients, {len(instructions)} steps")
    print(f"   cuisineType: {processed.get('cuisineType', {}).get('S')}")
    print(f"   isQuick: {processed.get('isQuick', {}).get('BOOL')}")
    print(f"   isBalanced: {processed.get('isBalanced', {}).get('BOOL')}")
    print(f"   isGourmet: {processed.get('isGourmet', {}).get('BOOL')}")
    print(f"\n   First 3 instructions:")
    for i, step in enumerate(instructions[:3], 1):
        print(f"   {i}. {step[:100]}...")
except json.JSONDecodeError as e:
    print(f"❌ JSON parse error: {e}")
    processed = None

print(f"\n{'='*80}")

# Step 6: QA
if processed:
    print(f"\n🤖 AGENT 6: QA Review...")
    sys.stdout.flush()
    
    qa_prompt = f"""
ORIGINAL RECIPE (from URL scrape):
{recipe_text[:2000]}

AGENT-PROCESSED VERSION:
{json.dumps(processed, indent=2)}
"""
    qa_result = qa_agent(qa_prompt)
    qa_text = get_message_text(qa_result)
    print(f"\n📊 QA REPORT:")
    print(qa_text)

print(f"\n{'='*80}")

# Step 7: Image Generation
if image_path and os.path.exists(image_path):
    print(f"\n🤖 AGENT 7: Image Generation...")
    print(f"   Source image: {image_path}")
    sys.stdout.flush()
    
    desc = processed.get('description', {}).get('S', title) if processed else 'Thai grilled beef'
    img_agent_prompt = f"""Generate recipe images for:
- dish_name: {title}
- dish_description: {desc}
- input_image_path: {image_path}
- output_prefix: /tmp/{recipe_id}
"""
    from strands_agents_recipe_creator import image_gen_agent
    img_result = image_gen_agent(img_agent_prompt)
    img_text = get_message_text(img_result)
    print(img_text)
else:
    print(f"\n⚠️  No source image available - skipping image generation")

print(f"\n{'='*80}")
print(f"🎉 PIPELINE COMPLETE")
print(f"Recipe: {title if processed else 'Unknown'}")
print(f"ID: {recipe_id}")
