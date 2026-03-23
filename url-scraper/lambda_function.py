import json
import re
import boto3
import requests
from bs4 import BeautifulSoup

STATE_MACHINE_ARN = 'arn:aws:states:us-west-2:023392223961:stateMachine:ez-recipe-creator-V2'
sfn_client = boto3.client('stepfunctions', region_name='us-west-2')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}


def fetch_page(url):
    """Fetch page HTML using requests with session for cookie handling."""
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(url, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def extract_jsonld_recipe(soup):
    """Extract recipe from JSON-LD script tags."""
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and '@graph' in data:
            items = data['@graph']

        for item in items:
            if not isinstance(item, dict):
                continue
            schema_type = item.get('@type', '')
            types = schema_type if isinstance(schema_type, list) else [schema_type]
            if 'Recipe' in types:
                return format_jsonld_recipe(item)
    return None


def format_jsonld_recipe(recipe):
    """Convert JSON-LD recipe to plain text."""
    parts = []
    name = recipe.get('name', '')
    if name:
        parts.append(f"Recipe: {name}")

    desc = recipe.get('description', '')
    if desc:
        # Clean HTML from description
        if '<' in desc:
            desc = BeautifulSoup(desc, 'html.parser').get_text()
        parts.append(f"\nDescription: {desc}")

    yield_val = recipe.get('recipeYield')
    if yield_val:
        if isinstance(yield_val, list):
            yield_val = yield_val[0]
        parts.append(f"\nServings: {yield_val}")

    for key, label in [('prepTime', 'Prep Time'), ('cookTime', 'Cook Time'), ('totalTime', 'Total Time')]:
        val = recipe.get(key, '')
        if val:
            parts.append(f"{label}: {parse_iso_duration(val)}")

    ingredients = recipe.get('recipeIngredient', [])
    if ingredients:
        parts.append("\nIngredients:")
        for ing in ingredients:
            parts.append(f"- {ing}")

    instructions = recipe.get('recipeInstructions', [])
    if instructions:
        parts.append("\nInstructions:")
        for i, step in enumerate(instructions, 1):
            if isinstance(step, dict):
                text = step.get('text', '')
            elif isinstance(step, str):
                text = step
            else:
                continue
            if text:
                if '<' in text:
                    text = BeautifulSoup(text, 'html.parser').get_text()
                parts.append(f"{i}. {text}")

    for key, label in [('recipeCategory', 'Category'), ('recipeCuisine', 'Cuisine')]:
        val = recipe.get(key)
        if val:
            if isinstance(val, list):
                val = ', '.join(val)
            parts.append(f"\n{label}: {val}")

    return '\n'.join(parts)


def parse_iso_duration(duration):
    """Convert ISO 8601 duration to readable string."""
    if not duration or not duration.startswith('PT'):
        return duration or ''
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return duration
    hours, mins, secs = match.groups()
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if int(hours) > 1 else ''}")
    if mins:
        parts.append(f"{mins} minutes")
    if secs:
        parts.append(f"{secs} seconds")
    return ', '.join(parts) if parts else duration


def extract_fallback(soup):
    """Extract recipe content from page structure when no JSON-LD found."""
    # Try common recipe content selectors (ordered by specificity)
    selectors = [
        '[itemtype*="schema.org/Recipe"]',
        'div.wprm-recipe-container', 'div.tasty-recipes',
        'div.recipe-card', 'div.recipe-body', 'div.recipe-content',
        'article.recipe', 'div[class*="recipe"]', 'div[class*="Recipe"]',
        'main article', 'main', 'article', 'div.entry-content',
        'div[role="main"]',
    ]
    content_el = None
    for sel in selectors:
        content_el = soup.select_one(sel)
        if content_el and len(content_el.get_text(strip=True)) > 100:
            break
        content_el = None

    if not content_el:
        content_el = soup.body

    if not content_el:
        raise ValueError("Could not find recipe content on page")

    # Remove unwanted elements
    for tag in content_el.find_all(['script', 'style', 'nav', 'footer', 'aside', 'iframe', 'svg', 'noscript']):
        tag.decompose()

    text = content_el.get_text(separator='\n', strip=True)
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 3]

    if len(lines) < 3:
        raise ValueError("Insufficient recipe content found on page")

    return '\n'.join(lines[:300])


def lambda_handler(event, context):
    url = event.get('url')
    if not url:
        raise ValueError("Missing required 'url' parameter")
    if not url.startswith('http'):
        raise ValueError(f"Invalid URL: {url}")

    html = fetch_page(url)
    soup = BeautifulSoup(html, 'html.parser')

    # Try JSON-LD first, then fallback
    recipe_text = extract_jsonld_recipe(soup)
    source_method = 'json-ld'

    if not recipe_text:
        recipe_text = extract_fallback(soup)
        source_method = 'fallback-html'

    recipe_text = f"{recipe_text}\n\nSource URL: {url}"

    response = sfn_client.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps({'recipe': recipe_text})
    )

    return {
        'statusCode': 200,
        'body': json.dumps({
            'executionArn': response['executionArn'],
            'sourceUrl': url,
            'extractionMethod': source_method,
            'recipePreview': recipe_text[:500]
        })
    }
