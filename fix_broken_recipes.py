#!/usr/bin/env python3
"""
Fix 13 broken recipes: assign proper product and side IDs.
Writes corrected JSON to S3 (menu-items-json bucket).
"""

import sys
sys.path.insert(0, '/tmp/pip-install')
import boto3
import json

# ============================================================
# SIDE CATALOG (id → title)
# ============================================================
SIDES = {
    "bd66acc6-3d36-40ab-b559-f8dc0a8c9f79": "Asian Rice",
    "80a712b4-7c62-4ddc-b583-3f4cc7d13065": "BBQ Grilled Veggies",
    "558a7c83-0814-417e-af31-cfc17c703db5": "Baked Beans",
    "3e5a8245-bff7-463f-8295-60ad4bad1601": "Basmati Rice (Indian)",
    "00702059-2dfa-4cf4-961c-7fda3331b1bd": "Basmati Rice (organic-frozen)",
    "d33a7b6a-caf5-4482-b108-4e7fc19606b1": "Caribbean Coconut Rice & Beans",
    "d36a24d9-bbe5-4ab8-abb8-af206fe4528d": "Cilantro Lime Crema",
    "197bf75c-57b3-40fd-9097-737c59cf6851": "Classic Coleslaw",
    "699126b9-f51d-4c70-985a-6f79d8547734": "Coconut Rice",
    "5b32253c-6ee8-45ea-81b3-a91281e13fba": "Cuban Black Beans",
    "c1f34ca7-c83a-424b-aeca-3ade9dc3a1fb": "French Fries",
    "9828f9f0-9be6-4b64-8563-d255234b9db3": "Fresh Garden Salad",
    "3d76e5bc-0e3b-477f-9caf-e146729bd179": "Fresh Guacamole (store-bought)",
    "4bb83a0d-0d42-4d7c-ab6b-8be3a02b4f1c": "Fresh Salsa",
    "453b0798-847b-4bb4-9284-96410e29afe1": "Garlic Bread",
    "ea69149d-d00c-4ffe-9cf8-c211071cb6a8": "Grilled Sweet Potatoes",
    "e094268d-3d62-449b-933b-65b7c819deee": "Homemade Caesar Salad",
    "37e2cac0-7fdb-4659-b5bc-0bef5686aaa0": "Homemade Guacamole",
    "e931ab8d-112e-4126-b3d8-ac0b435abd64": "Homemade Ranch Dressing",
    "d52741b0-689f-4e1f-a6fc-3aa24322a04d": "Iceberg Wedge Salad",
    "b4efe05b-db9e-456c-9619-d7ae393961a1": "Jasmine Rice (organic-frozen)",
    "fff9f91e-3fb1-4368-99db-abfa0da7cf03": "Kachumber Salad",
    "e6bac67b-32ea-4e4b-ab5c-eecba841892a": "Mango-Avocado Salsa",
    "9dc8a6b1-1da3-4d71-87d0-a6bc85a0ad59": "Mashed Potatoes",
    "bfb7d782-4421-4bd4-9e8a-fa3059ba6a0b": "Mexican Rice",
    "572cbdc3-03f0-425e-91ec-115daccd12d4": "Naan Bread",
    "7f32f56f-02e3-4829-908a-3bc3bfa2a96a": "Naan Bread (frozen)",
    "13a71340-1882-4d32-ab24-d4abfb703299": "Pico de Gallo",
    "0da3bc9c-901b-4774-9bd0-4c106e95c921": "Roasted Broccolini",
    "b9b2cc09-321a-4f26-b610-b8affbf12cbb": "Simple Romaine Side Salad",
    "0f65b2cf-b0fe-4542-9d9d-b829fa0b7a6f": "Spanish Rice",
    "615d1420-7027-4699-96a6-705a2d9fe7a3": "Store-Bought Artisan Bread",
    "f34f8af2-02e6-4a3a-949d-63d406447411": "Sweet Potato Fries (frozen)",
    "8f6c70ef-25f5-455c-86d4-fe8294c049ce": "Teriyaki Pasta Salad",
    "c9c8182f-95c4-41cd-811d-5dfacc787596": "Thai Cucumber Salad",
    "2d23e3ad-b1ed-41c6-ad1f-76831111b51e": "Tomato Cucumber Salad",
    "c8a28f0e-af95-4b93-ace5-7153134772c2": "Tortilla Chips",
    "82a5c75d-59db-450b-978c-7c520f80faf6": "Tostones",
    "374b2cc1-f060-4cfe-bdf5-b1e62a6846fb": "White Rice",
}

# ============================================================
# PRODUCT CATALOG — TOOLS/EQUIPMENT ONLY (no food items)
# ============================================================
PRODUCTS_TOOLS = {
    "ba602838-93f6-4e80-9030-3b58d28e8725": "Cast Iron Skillet",
    "76cda222-84b6-4a7c-8c89-8df62e963b4e": "Heavy Duty Grill Accessories",
    "cf638481-5d13-4692-8cb3-3cb21b8b42fd": "6 Quart Programmable Slow Cooker",
    "3394059c-775a-43e4-801f-4192b82c2714": "BBQ Basting Brush",
    "d14e781b-31cb-4196-a83f-29feb9bfdde0": "Ceramic Baking Dishes",
    "ea214b85-69dd-49b7-867e-17409204194e": "Cooking Oil Spray/Pour Dispenser",
    "88d20131-5a87-4032-b475-50813f37e0b1": "Digital Meat Thermometer",
    "217cf6d2-237f-493d-9bd3-02ac21f3d078": "Extra Large Walnut Wood Cutting Board",
    "c65f1e9e-c6b8-4d97-a449-5a10c7e63639": "Gorilla Grip Granite Mortar and Pestle",
    "650e3cfd-9c6e-495c-b7b8-eb682de555ae": "Stainless Steel Skewers",
    "a788e5c6-b12c-47d6-8613-830def051245": "Hand-Painted Ceramic Tortilla Warmer",
    "81cf6862-8f59-48ab-a7f5-2b545fc904cf": "Instant Pot",
    "65f66c65-9e6b-448a-b079-3ef8fea506b1": "Le Creuset Dutch Oven",
    "f63e51da-bf1e-49a0-8b6f-0c1879678291": "Acacia Wave Serving Bowl",
    "60eaf0c2-831b-4df3-adb5-cb5f43777bbe": "Lodge 6 Qt Dutch Oven",
    "ef027721-e2f1-4f35-9fe5-01387e7ff639": "Carbon Steel Baking Sheets",
    "4fd06a65-5fd3-436b-a7c8-480ad311ba36": "Ceramic Sauce Pan",
    "d6e52aaf-79d6-4621-a05e-474e970c9808": "Over The Sink Stainless Steel Colander",
    "77024055-0755-4cab-89d8-3bfc2bcc9869": "Vitamix Blender",
    "650e3cfd-9c6e-495c-b7b8-eb682de777ae": "Silicone Microwave Covers",
    "5f8f54ff-991f-4e19-a4d9-3d5c476e36f0": "Cooling and Baking Rack",
    "3dc11c5a-5c87-46fc-8fbc-968fcf7a13c6": "Dough Scraper/Cutter",
    "904cc1ac-66e5-497f-b3bb-e0f89c76b0e9": "Stainless Steel Grill Basket",
    "76c32811-3974-40e2-92a0-7b0f4350e547": "Stainless Steel Taco Holders",
    "60de80a1-a040-4677-a5d1-5b968991bf76": "Sticky Rice Bamboo Steamer Basket",
    "63e5e38f-3f92-4d6b-b398-0337198f4f05": "Tostones Plantain Press",
    "650e3cfd-9c6e-495c-b7b8-eb682de444ae": "Carbon Steel Pow Wok",
    "be9ca3ed-f95a-40af-806d-bf110a25a761": "Vegetable Chopper",
    "3e63f6a0-04f9-4f72-bcb1-28c2598658c6": "Meat Chopper",
    "650e3cfd-9c6e-495c-b7b8-eb682de586ae": "Avocado Slicer",
}

# ============================================================
# ASSIGNMENTS — hand-picked to make culinary sense
# ============================================================
FIXES = {
    # --- NEEDS PRODUCTS ONLY ---
    "AB12D5FE-64D9-4920-AB56-5C11312936A7": {  # Asian Chicken Lettuce Wraps
        "title": "Asian Chicken Lettuce Wraps",
        "products": [
            "be9ca3ed-f95a-40af-806d-bf110a25a761",  # Vegetable Chopper — lots of fine chopping
            "650e3cfd-9c6e-495c-b7b8-eb682de444ae",  # Carbon Steel Wok — stir-fry the filling
        ],
    },
    "2DD374A0-0D78-42D7-8155-1909AC10CD85": {  # Chicken Fajita Bowls
        "title": "Chicken Fajita Bowls",
        "products": [
            "ba602838-93f6-4e80-9030-3b58d28e8725",  # Cast Iron Skillet — sear chicken & peppers
            "be9ca3ed-f95a-40af-806d-bf110a25a761",  # Vegetable Chopper — bell peppers & onion
            "88d20131-5a87-4032-b475-50813f37e0b1",  # Digital Meat Thermometer — chicken doneness
        ],
    },
    "C824D3A9-77A9-4943-B132-550C5371F6A2": {  # Homemade American Goulash
        "title": "Homemade American Goulash",
        "products": [
            "60eaf0c2-831b-4df3-adb5-cb5f43777bbe",  # Lodge Dutch Oven — one-pot dish
            "3e63f6a0-04f9-4f72-bcb1-28c2598658c6",  # Meat Chopper — break up ground beef
            "d6e52aaf-79d6-4621-a05e-474e970c9808",  # Colander — drain pasta
        ],
    },
    "5C3DBEFD-E03E-4846-BBE6-9BFA66D867B8": {  # Jamaican Jerk Chicken
        "title": "Jamaican Jerk Chicken",
        "products": [
            "ba602838-93f6-4e80-9030-3b58d28e8725",  # Cast Iron Skillet — sear chicken
            "88d20131-5a87-4032-b475-50813f37e0b1",  # Digital Meat Thermometer — chicken doneness
        ],
    },
    "3407e619-4739-4348-b940-1be2e2f9575b": {  # Orange Peel Chicken
        "title": "Orange Peel Chicken",
        "products": [
            "650e3cfd-9c6e-495c-b7b8-eb682de444ae",  # Carbon Steel Wok — deep fry + stir-fry
            "88d20131-5a87-4032-b475-50813f37e0b1",  # Digital Meat Thermometer — oil temp
        ],
    },
    "2EAC8B4C-F539-4B7B-95BD-633939E9394F": {  # Ropa Vieja
        "title": "Ropa Vieja",
        "products": [
            "60eaf0c2-831b-4df3-adb5-cb5f43777bbe",  # Lodge Dutch Oven — braising
            "217cf6d2-237f-493d-9bd3-02ac21f3d078",  # Cutting Board — shredding meat
            "88d20131-5a87-4032-b475-50813f37e0b1",  # Digital Meat Thermometer — fork tender
        ],
    },
    "31CA6C00-CDEC-450F-903C-15A6F993ED36": {  # Spanish Lentil Stew
        "title": "Spanish Lentil Stew",
        "products": [
            "60eaf0c2-831b-4df3-adb5-cb5f43777bbe",  # Lodge Dutch Oven — stew pot
            "be9ca3ed-f95a-40af-806d-bf110a25a761",  # Vegetable Chopper — all the veg prep
        ],
    },
    # --- NEEDS SIDES ONLY ---
    "ead8ee09-6737-4f2e-a90d-59542a119df2": {  # Crusted Honey Mustard Chicken Salad
        "title": "Crusted Honey Mustard Chicken Salad",
        "sides": [
            "615d1420-7027-4699-96a6-705a2d9fe7a3",  # Store-Bought Artisan Bread
            "c1f34ca7-c83a-424b-aeca-3ade9dc3a1fb",  # French Fries — classic with chicken salad
            "f34f8af2-02e6-4a3a-949d-63d406447411",  # Sweet Potato Fries
        ],
    },
    # --- NEEDS BOTH PRODUCTS + SIDES ---
    "14fd9c52-189b-4b1c-8897-e30ce3582380": {  # Chicken Kabobs (Teriyaki-Style)
        "title": "Chicken Kabobs (Teriyaki-Style)",
        "products": [
            "650e3cfd-9c6e-495c-b7b8-eb682de555ae",  # Stainless Steel Skewers
            "904cc1ac-66e5-497f-b3bb-e0f89c76b0e9",  # Stainless Steel Grill Basket
            "88d20131-5a87-4032-b475-50813f37e0b1",  # Digital Meat Thermometer
        ],
        "sides": [
            "bd66acc6-3d36-40ab-b559-f8dc0a8c9f79",  # Asian Rice
            "c9c8182f-95c4-41cd-811d-5dfacc787596",  # Thai Cucumber Salad
            "8f6c70ef-25f5-455c-86d4-fe8294c049ce",  # Teriyaki Pasta Salad
            "0da3bc9c-901b-4774-9bd0-4c106e95c921",  # Roasted Broccolini
        ],
    },
    "c7885f16-7d9a-46ea-8c49-84082c14cc62": {  # Mongolian Noodles
        "title": "Mongolian Noodles",
        "products": [
            "650e3cfd-9c6e-495c-b7b8-eb682de444ae",  # Carbon Steel Wok — stir-fry
            "3e63f6a0-04f9-4f72-bcb1-28c2598658c6",  # Meat Chopper — ground meat
            "d6e52aaf-79d6-4621-a05e-474e970c9808",  # Colander — drain noodles
        ],
        "sides": [
            "c9c8182f-95c4-41cd-811d-5dfacc787596",  # Thai Cucumber Salad — cool contrast
            "0da3bc9c-901b-4774-9bd0-4c106e95c921",  # Roasted Broccolini — green veg
            "2d23e3ad-b1ed-41c6-ad1f-76831111b51e",  # Tomato Cucumber Salad — fresh balance
        ],
    },
    "93ba29ae-c275-4de6-95f5-909788861f39": {  # Pad Thai
        "title": "Pad Thai",
        "products": [
            "650e3cfd-9c6e-495c-b7b8-eb682de444ae",  # Carbon Steel Wok — essential for pad thai
            "d6e52aaf-79d6-4621-a05e-474e970c9808",  # Colander — drain rice noodles
        ],
        "sides": [
            "c9c8182f-95c4-41cd-811d-5dfacc787596",  # Thai Cucumber Salad
            "b4efe05b-db9e-456c-9619-d7ae393961a1",  # Jasmine Rice (organic-frozen)
            "0da3bc9c-901b-4774-9bd0-4c106e95c921",  # Roasted Broccolini
        ],
    },
    "d998fc3b-1311-4812-9510-11b6e6a8622d": {  # Quick and Easy Pho
        "title": "Quick and Easy Pho",
        "products": [
            "60eaf0c2-831b-4df3-adb5-cb5f43777bbe",  # Lodge Dutch Oven — broth pot
            "d6e52aaf-79d6-4621-a05e-474e970c9808",  # Colander — noodles
            "f63e51da-bf1e-49a0-8b6f-0c1879678291",  # Serving Bowl — pho bowls
        ],
        "sides": [
            "bd66acc6-3d36-40ab-b559-f8dc0a8c9f79",  # Asian Rice — optional side
            "c9c8182f-95c4-41cd-811d-5dfacc787596",  # Thai Cucumber Salad
            "9828f9f0-9be6-4b64-8563-d255234b9db3",  # Fresh Garden Salad
        ],
    },
    "2311b128-ee47-4f7d-99f6-b615a35c43aa": {  # Thai Basil Chicken
        "title": "Thai Basil Chicken",
        "products": [
            "650e3cfd-9c6e-495c-b7b8-eb682de444ae",  # Carbon Steel Wok — stir-fry
            "3e63f6a0-04f9-4f72-bcb1-28c2598658c6",  # Meat Chopper — ground chicken
        ],
        "sides": [
            "b4efe05b-db9e-456c-9619-d7ae393961a1",  # Jasmine Rice — essential pairing
            "c9c8182f-95c4-41cd-811d-5dfacc787596",  # Thai Cucumber Salad
            "0da3bc9c-901b-4774-9bd0-4c106e95c921",  # Roasted Broccolini
            "9828f9f0-9be6-4b64-8563-d255234b9db3",  # Fresh Garden Salad
        ],
    },
}


def get_creds():
    sts = boto3.client('sts')
    return sts.assume_role(
        RoleArn='arn:aws:iam::970547358447:role/CrossAccountDynamoDBWriter',
        RoleSessionName='fix-recipes'
    )['Credentials']


def get_recipe_from_db(dynamodb, recipe_id):
    resp = dynamodb.get_item(
        TableName='MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev',
        Key={'id': {'S': recipe_id}}
    )
    return resp.get('Item')


def fix_recipes():
    creds = get_creds()
    dynamodb = boto3.client('dynamodb',
        region_name='us-west-1',
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken']
    )
    s3 = boto3.client('s3',
        region_name='us-west-1',
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken']
    )

    for recipe_id, fix in FIXES.items():
        title = fix['title']
        print(f"\n{'='*50}")
        print(f"Fixing: {title} ({recipe_id})")

        # Get current recipe from DynamoDB
        item = get_recipe_from_db(dynamodb, recipe_id)
        if not item:
            print(f"  ❌ Not found in DynamoDB!")
            continue

        # Build update expression
        update_parts = []
        expr_values = {}

        if 'products' in fix:
            product_list = [{"S": pid} for pid in fix['products']]
            update_parts.append("products = :products")
            expr_values[':products'] = {"L": product_list}
            print(f"  Setting {len(fix['products'])} products:")
            for pid in fix['products']:
                print(f"    - {PRODUCTS_TOOLS.get(pid, pid)}")

        if 'sides' in fix:
            side_list = [{"S": sid} for sid in fix['sides']]
            update_parts.append("recommendedSides = :sides")
            expr_values[':sides'] = {"L": side_list}
            print(f"  Setting {len(fix['sides'])} sides:")
            for sid in fix['sides']:
                print(f"    - {SIDES.get(sid, sid)}")

        if not update_parts:
            print(f"  ⏭️  Nothing to fix")
            continue

        # Update DynamoDB directly — these are metadata corrections, not recipe data
        dynamodb.update_item(
            TableName='MenuItemData-ryvykzwfevawxbpf5nmynhgtea-dev',
            Key={'id': {'S': recipe_id}},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=expr_values,
        )
        print(f"  ✅ DynamoDB updated")

        # Also update S3 JSON if it exists
        s3_key = title.replace(' ', '_') + '.json'
        try:
            resp = s3.get_object(Bucket='menu-items-json', Key=s3_key)
            recipe_json = json.loads(resp['Body'].read())

            if 'products' in fix:
                recipe_json['products'] = fix['products']
            if 'sides' in fix:
                recipe_json['recommendedSides'] = fix['sides']

            s3.put_object(
                Bucket='menu-items-json',
                Key=s3_key,
                Body=json.dumps(recipe_json, indent=2),
                ContentType='application/json'
            )
            print(f"  ✅ S3 updated ({s3_key})")
        except Exception as e:
            print(f"  ⚠️  S3 update skipped ({e})")


def verify_fixes():
    """Verify all 13 recipes now have proper UUID products and sides."""
    creds = get_creds()
    dynamodb = boto3.client('dynamodb',
        region_name='us-west-1',
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken']
    )

    print(f"\n{'='*50}")
    print("VERIFICATION")
    print(f"{'='*50}")

    all_good = True
    for recipe_id, fix in FIXES.items():
        item = get_recipe_from_db(dynamodb, recipe_id)
        title = fix['title']

        products = item.get('products', {}).get('L', [])
        sides = item.get('recommendedSides', {}).get('L', [])

        prod_ids = [p.get('S', '') for p in products if 'S' in p]
        prod_objects = [p for p in products if 'M' in p]
        side_ids = [s.get('S', '') for s in sides if 'S' in s]
        side_objects = [s for s in sides if 'M' in s]

        issues = []
        if 'products' in fix and (len(prod_ids) == 0 or len(prod_objects) > 0):
            issues.append(f"products bad ({len(prod_ids)} ids, {len(prod_objects)} objects)")
        if 'sides' in fix and (len(side_ids) == 0 or len(side_objects) > 0):
            issues.append(f"sides bad ({len(side_ids)} ids, {len(side_objects)} objects)")

        if issues:
            print(f"  ❌ {title}: {', '.join(issues)}")
            all_good = False
        else:
            print(f"  ✅ {title}: {len(prod_ids)} products, {len(side_ids)} sides")

    return all_good


if __name__ == "__main__":
    fix_recipes()
    ok = verify_fixes()
    if ok:
        print("\n🎉 ALL 13 RECIPES FIXED AND VERIFIED")
    else:
        print("\n❌ SOME RECIPES STILL BROKEN")
