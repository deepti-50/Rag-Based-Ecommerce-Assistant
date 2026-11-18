"""
Seed the catalog with 200 realistic (fictional-brand) products across 8 categories.

    python -m app.scripts.seed            # only if the catalog is empty
    python -m app.scripts.seed --reset    # DEV ONLY: wipe catalog (+ carts/orders/reviews pointing to it) and reseed
    docker compose exec api python -m app.scripts.seed

Deterministic (fixed random seed): every run produces the same products, which keeps
RAG experiments and evaluations comparable.

Data rule: features and uses are defined PER PRODUCT TYPE, so a description can never
claim something impossible (e.g. a power bank with noise cancellation). The RAG
assistant will repeat whatever these descriptions say, so they must be true.
"""
import asyncio
import random
import sys
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.database import async_session, engine
from app.models.product import Category, Product

PRODUCTS_PER_CATEGORY = 25
COLORS = ["Black", "Navy", "Olive", "Charcoal", "White", "Maroon", "Beige", "Teal", "Mustard", "Grey"]


@dataclass(frozen=True)
class T:
    """One product type: price range in INR, attribute choices, true features, typical uses."""
    name: str
    price: tuple[int, int]
    attrs: dict[str, list[str]]
    features: list[str]
    uses: list[str]


CLOTHING_SIZES_M = ["S", "M", "L", "XL", "XXL"]
CLOTHING_SIZES_W = ["XS", "S", "M", "L", "XL"]
SHOE_SIZES = ["UK 6", "UK 7", "UK 8", "UK 9", "UK 10", "UK 11"]

CATALOG: dict[str, dict] = {
    "Men's Clothing": {
        "description": "T-shirts, shirts, hoodies, jeans and jackets for men.",
        "brands": ["Urban Thread", "Northpeak", "Kosha"],
        "sizes": CLOTHING_SIZES_M,
        "types": [
            T("Crew Neck T-Shirt", (399, 1299),
              {"material": ["100% cotton", "cotton-modal blend", "organic cotton"], "fit": ["regular", "slim", "oversized"]},
              ["breathable fabric that stays comfortable in hot weather", "pre-shrunk fabric that keeps its size after washing",
               "ribbed neckline that doesn't stretch out"],
              ["everyday wear", "college", "weekend outings"]),
            T("Oxford Shirt", (899, 2499),
              {"material": ["cotton oxford", "linen-cotton blend"], "fit": ["regular", "slim"]},
              ["button-down collar that stays crisp all day","wrinkle-resistant finish",
               "soft fabric that gets better with every wash"],
              ["office wear", "smart-casual dinners", "interviews"]),
            T("Pullover Hoodie", (999, 2999),
              {"material": ["fleece-lined cotton", "French terry"], "fit": ["regular", "relaxed"]},
              ["kangaroo pocket and adjustable drawstring hood", "warm brushed inner lining",
               "ribbed cuffs that keep the cold out"],
              ["winter evenings", "travel", "lounging at home"]),
            T("Stretch Jeans", (1199, 3499),
              {"material": ["stretch denim", "rigid selvedge denim"], "fit": ["slim", "straight", "tapered"]},
              ["2% stretch for easy movement", "reinforced stitching at stress points",
               "colour-fast wash that won't fade quickly"],
              ["everyday wear", "weekend outings", "travel"]),
            T("Quilted Jacket", (1999, 5999),
              {"material": ["nylon shell with synthetic fill", "recycled polyester"], "fit": ["regular"]},
              ["water-repellent outer shell", "a design that packs into its own pocket",
               "lightweight insulation that is warm without bulk"],
              ["winter travel", "hill-station trips", "chilly commutes"]),
        ],
    },
    "Women's Clothing": {
        "description": "Kurtas, dresses, tops and activewear for women.",
        "brands": ["Kosha", "Saanjh", "Urban Thread"],
        "sizes": CLOTHING_SIZES_W,
        "types": [
            T("Straight Kurta", (599, 2499),
              {"material": ["cotton", "rayon", "chanderi silk blend"], "fit": ["straight", "A-line"]},
              ["hand block-printed motifs", "side slits for easy movement", "deep side pockets"],
              ["festive occasions", "office wear", "daily wear"]),
            T("Wrap Dress", (999, 3499),
              {"material": ["crepe", "viscose", "georgette"], "fit": ["regular"]},
              ["adjustable tie waist that flatters every body type", "lightweight drape that flows well",
               "a full lining so it isn't see-through"],
              ["brunch", "date nights", "office parties"]),
            T("Relaxed Top", (499, 1499),
              {"material": ["cotton", "linen blend", "modal"], "fit": ["relaxed", "regular"]},
              ["breathable fabric for humid days", "a cut that is easy to pair with jeans or trousers", "soft, non-itchy seams"],
              ["daily wear", "college", "travel"]),
            T("High-Rise Leggings", (499, 1799),
              {"material": ["cotton-spandex", "nylon-spandex"], "fit": ["skinny"]},
              ["four-way stretch", "squat-proof, opaque fabric", "wide waistband that doesn't roll down"],
              ["yoga", "gym workouts", "daily wear"]),
            T("Palazzo Pants", (599, 1999),
              {"material": ["rayon", "cotton flex"], "fit": ["flared"]},
              ["elasticated waist for all-day comfort", "flowy wide-leg cut", "a cut that pairs well with kurtas and tops"],
              ["daily wear", "festive occasions", "travel"]),
        ],
    },
    "Footwear": {
        "description": "Running shoes, sneakers, sandals and formal shoes.",
        "brands": ["StrideX", "Terra Trails", "Northpeak"],
        "sizes": SHOE_SIZES,
        "types": [
            T("Running Shoes", (1499, 6999),
              {"upper": ["engineered mesh", "knit upper"], "sole": ["EVA foam", "rubber with EVA midsole"]},
              ["responsive cushioning that absorbs impact", "breathable upper that keeps feet cool",
               "lightweight build that reduces fatigue on long runs"],
              ["daily running", "gym sessions", "brisk walks"]),
            T("Canvas Sneakers", (799, 2499),
              {"upper": ["cotton canvas"], "sole": ["vulcanised rubber"]},
              ["classic low-top design", "cushioned footbed", "machine-washable canvas"],
              ["everyday wear", "college", "weekend outings"]),
            T("Trail Sandals", (899, 2999),
              {"upper": ["synthetic straps", "nubuck leather straps"], "sole": ["grippy rubber"]},
              ["anti-slip outsole that grips on wet surfaces", "quick-drying straps", "adjustable hook-and-loop fit"],
              ["monsoon commutes", "beach trips", "light treks"]),
            T("Leather Loafers", (1999, 5499),
              {"upper": ["genuine leather", "suede"], "sole": ["TPR", "leather"]},
              ["slip-on design with a padded collar", "hand-stitched moccasin toe", "cushioned leather insole"],
              ["office wear", "weddings", "smart-casual outings"]),
            T("Hiking Boots", (2999, 7999),
              {"upper": ["waterproof leather", "mesh with waterproof membrane"], "sole": ["lugged rubber"]},
              ["waterproof construction that keeps feet dry", "ankle support for uneven trails",
               "deep lugs for grip on mud and rock"],
              ["weekend treks", "Himalayan hikes", "camping"]),
        ],
    },
    "Electronics": {
        "description": "Audio, wearables, chargers and smart-home gadgets.",
        "brands": ["Voltix", "Sonora", "Pixelon"],
        "sizes": [],
        "types": [
            T("Wireless Earbuds", (999, 7999),
              {"battery_life": ["24 hours with case", "30 hours with case", "40 hours with case"], "connectivity": ["Bluetooth 5.3", "Bluetooth 5.4"]},
              ["active noise cancellation that cuts out traffic noise", "IPX5 sweat resistance",
               "low-latency mode for gaming and video calls"],
              ["commuting", "workouts", "work-from-home calls"]),
            T("Over-Ear Headphones", (1999, 12999),
              {"battery_life": ["40 hours", "60 hours"], "connectivity": ["Bluetooth 5.3", "Bluetooth + 3.5mm wired"]},
              ["active noise cancellation for flights and open offices", "soft memory-foam ear cushions",
               "companion app with custom EQ"],
              ["travel", "focused work", "music listening"]),
            T("Smartwatch", (1999, 9999),
              {"battery_life": ["7 days", "10 days", "14 days"], "display": ["1.43-inch AMOLED", "1.96-inch AMOLED"]},
              ["heart-rate, SpO2 and sleep tracking", "Bluetooth calling from the wrist", "100+ sports modes"],
              ["fitness tracking", "daily wear", "workouts"]),
            T("Power Bank", (799, 3499),
              {"capacity": ["10000 mAh", "20000 mAh", "27000 mAh"], "ports": ["USB-C PD", "USB-C + USB-A"]},
              ["fast charging up to 22.5W", "dual outputs to charge two devices at once", "LED indicator showing remaining charge"],
              ["travel", "long workdays", "power cuts"]),
            T("Smart Speaker", (1499, 6999),
              {"power": ["mains powered", "12-hour rechargeable battery"], "connectivity": ["Wi-Fi + Bluetooth"]},
              ["a built-in voice assistant", "room-filling 360° sound", "control of compatible smart lights and plugs"],
              ["home entertainment", "smart-home control", "kitchen music"]),
        ],
    },
    "Home & Kitchen": {
        "description": "Cookware, storage, bedding and home decor.",
        "brands": ["Aurum Home", "Ghar Craft", "Kosha"],
        "sizes": [],
        "types": [
            T("Non-Stick Kadai", (699, 2499),
              {"material": ["granite-coated aluminium", "ceramic-coated aluminium"], "capacity": ["2 L", "3 L", "4 L"]},
              ["compatibility with gas stoves and induction", "PFOA-free non-stick coating", "a matching glass lid"],
              ["daily cooking", "deep frying", "a new home setup"]),
            T("Stainless Steel Pressure Cooker", (1199, 3999),
              {"material": ["tri-ply stainless steel", "stainless steel"], "capacity": ["3 L", "5 L"]},
              ["compatibility with gas stoves and induction", "safety valve and gasket-release system", "even heating base"],
              ["daily cooking", "dal and rice", "meal prep"]),
            T("Glass Storage Jar Set", (499, 1799),
              {"material": ["borosilicate glass with airtight lid"], "pieces": ["set of 3", "set of 6"]},
              ["an airtight seal that keeps food fresh longer", "microwave- and dishwasher-safe glass", "a stackable design that saves space"],
              ["pantry organisation", "meal prep", "gifting"]),
            T("Cotton Bedsheet", (799, 2999),
              {"material": ["300 TC cotton", "400 TC cotton sateen"], "bed_size": ["double", "king"]},
              ["two matching pillow covers", "fade-resistant colours", "soft and breathable for warm nights"],
              ["bedroom refresh", "a new home setup", "gifting"]),
            T("Ceramic Planter", (399, 1499),
              {"material": ["glazed ceramic", "terracotta"], "planter_size": ["small", "medium", "large"]},
              ["drainage hole with a matching saucer", "frost- and fade-resistant glaze", "gift-ready packaging"],
              ["balcony gardening", "indoor plants", "gifting"]),
        ],
    },
    "Beauty & Personal Care": {
        "description": "Skincare, haircare and grooming essentials.",
        "brands": ["Lumen Botanics", "Saanjh", "Pure Root"],
        "sizes": [],
        "types": [
            T("Vitamin C Face Serum", (399, 1299),
              {"skin_type": ["all skin types", "oily skin", "dull skin"], "volume": ["30 ml", "50 ml"]},
              ["a formula that helps fade dark spots and brighten skin", "lightweight, non-greasy texture", "a fragrance-free formula"],
              ["a morning skincare routine", "uneven skin tone", "gifting"]),
            T("Gel Moisturiser", (299, 999),
              {"skin_type": ["oily skin", "combination skin", "all skin types"], "volume": ["50 ml", "100 ml"]},
              ["oil-free gel that absorbs quickly", "hyaluronic acid for hydration", "a non-comedogenic formula"],
              ["a daily skincare routine", "humid weather", "post-sun care"]),
            T("Mineral Sunscreen SPF 50", (349, 999),
              {"skin_type": ["sensitive skin", "all skin types"], "volume": ["50 ml", "80 ml"]},
              ["broad-spectrum SPF 50 PA++++ protection", "a finish that leaves no white cast", "40-minute water resistance"],
              ["daily sun protection", "outdoor sports", "beach trips"]),
            T("Argan Hair Oil", (299, 899),
              {"hair_type": ["dry and frizzy hair", "all hair types"], "volume": ["100 ml", "200 ml"]},
              ["a formula that tames frizz without weighing hair down", "a paraben- and sulphate-free formula", "light, non-sticky formula"],
              ["a weekly hair-care routine", "humid weather", "heat-styled hair"]),
            T("Beard Grooming Kit", (499, 1499),
              {"contents": ["beard oil, balm and comb", "beard oil, wash and scissors"], "skin_type": ["all skin types"]},
              ["oils that soften coarse beard hair", "ingredients that reduce itchiness and flakes", "a travel pouch"],
              ["a daily grooming routine", "travel", "gifting"]),
        ],
    },
    "Sports & Fitness": {
        "description": "Yoga, gym, cycling and outdoor gear.",
        "brands": ["Northpeak", "StrideX", "Terra Trails"],
        "sizes": [],
        "types": [
            T("Yoga Mat", (599, 2499),
              {"material": ["TPE", "natural rubber", "PVC"], "thickness": ["4 mm", "6 mm", "8 mm"]},
              ["non-slip grip even when sweaty", "a carry strap", "cushioning that protects knees"],
              ["yoga practice", "home workouts", "stretching"]),
            T("Adjustable Dumbbell Set", (1999, 7999),
              {"material": ["cast iron with rubber coating"], "total_weight": ["10 kg", "20 kg"]},
              ["quick-lock collars to change weight in seconds", "knurled grip handles", "a rubber coating that protects floors"],
              ["home workouts", "strength training", "beginners building a home gym"]),
            T("Resistance Band Set", (399, 1299),
              {"material": ["natural latex", "fabric"], "levels": ["5 bands, light to heavy"]},
              ["five resistance levels in one set", "an exercise guide", "a small carry pouch"],
              ["home workouts", "physiotherapy", "travel workouts"]),
            T("Insulated Water Bottle", (499, 1499),
              {"material": ["double-wall stainless steel"], "capacity": ["750 ml", "1 L"]},
              ["insulation that keeps drinks cold for 24 hours or hot for 12", "leak-proof lid", "BPA-free"],
              ["gym training", "office", "outdoor runs"]),
            T("Cycling Gloves", (399, 1199),
              {"material": ["synthetic leather palm with mesh back"], "padding": ["gel padded"]},
              ["gel padding that reduces hand numbness", "breathable mesh back", "touchscreen-friendly fingertips"],
              ["cycling", "gym training", "motorbike commutes"]),
        ],
    },
    "Bags & Accessories": {
        "description": "Backpacks, wallets, watches and travel accessories.",
        "brands": ["Urban Thread", "Terra Trails", "Aurum Home"],
        "sizes": [],
        "types": [
            T("Laptop Backpack", (999, 3999),
              {"material": ["water-resistant polyester", "recycled nylon"], "capacity": ["20 L", "28 L", "35 L"]},
              ["padded compartment that fits a 15.6-inch laptop", "USB charging port", "hidden anti-theft back pocket"],
              ["daily commuting", "college", "business travel"]),
            T("Leather Wallet", (499, 1999),
              {"material": ["genuine leather", "vegan leather"], "style": ["bifold", "card holder"]},
              ["RFID-blocking card slots", "a slim profile that fits a front pocket", "a gift box"],
              ["everyday carry", "gifting", "travel"]),
            T("Cabin Trolley Bag", (2499, 7999),
              {"material": ["polycarbonate hard shell", "ABS hard shell"], "size": ["55 cm cabin", "65 cm check-in"]},
              ["360° spinner wheels", "TSA-approved combination lock", "an expandable zip for extra space"],
              ["business travel", "family holidays", "weekend trips"]),
            T("Analog Watch", (999, 4999),
              {"strap": ["leather strap", "stainless steel mesh strap"], "dial": ["40 mm", "42 mm"]},
              ["Japanese quartz movement", "scratch-resistant mineral glass", "water resistance to 50 m"],
              ["office wear", "gifting", "everyday wear"]),
            T("Sling Bag", (599, 1999),
              {"material": ["canvas", "nylon"], "capacity": ["5 L", "8 L"]},
              ["adjustable crossbody strap", "room for a 10-inch tablet", "water-resistant zips"],
              ["day trips", "festivals and concerts", "daily commuting"]),
        ],
    },
}


def _price(rng: random.Random, low: int, high: int) -> Decimal:
    """Indian-retail style prices that end in 99 (e.g. 1,499)."""
    return Decimal(max(rng.randint(low // 100, high // 100) * 100 - 1, 99))


def _slug(name: str) -> str:
    return "-".join(name.lower().replace("&", "and").replace("'", "").split())


def build_products(rng: random.Random, spec: dict) -> list[dict]:
    products = []
    for i in range(PRODUCTS_PER_CATEGORY):
        ptype: T = spec["types"][i % len(spec["types"])]
        brand = rng.choice(spec["brands"])
        color = rng.choice(COLORS)
        chosen = {k: rng.choice(v) for k, v in ptype.attrs.items()}
        attributes = {"brand": brand, "color": color, **chosen}
        if spec["sizes"]:
            attributes["sizes"] = spec["sizes"]

        f1, f2 = rng.sample(ptype.features, 2)
        use = rng.choice(ptype.uses)
        details = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in chosen.items())
        # Fixed "label: value" layout, so any feature phrase reads correctly
        # (no "It has BPA-free" or "The Running Shoes is" grammar problems).
        description = (
            f"{brand} {ptype.name} in {color.lower()}, ideal for {use}. "
            f"Key features: {f1}; {f2}. Details: {details}."
        )
        roll = rng.random()  # ~10% out of stock, ~15% low stock, rest healthy
        stock = 0 if roll < 0.10 else rng.randint(1, 8) if roll < 0.25 else rng.randint(10, 200)

        products.append({
            "name": f"{brand} {ptype.name} - {color}",
            "description": description,
            "price": _price(rng, *ptype.price),
            "stock_quantity": stock,
            "attributes": attributes,
            "avg_rating": round(rng.uniform(3.2, 4.9), 1),
        })
    return products


async def main(reset: bool) -> int:
    rng = random.Random(42)
    async with async_session() as db:
        existing = await db.scalar(select(func.count()).select_from(Product))
        if existing and not reset:
            print(f"Catalog already has {existing} products; nothing done. Use --reset to wipe and reseed (dev only).")
            return 0
        if reset:
            await db.execute(text("TRUNCATE products, categories CASCADE"))
            print("Wiped existing catalog (and carts/orders/reviews referencing it).")

        total = 0
        for name, spec in CATALOG.items():
            category = Category(name=name, slug=_slug(name), description=spec["description"])
            db.add(category)
            await db.flush()  # assigns category.id
            for data in build_products(rng, spec):
                db.add(Product(category_id=category.id, **data))
                total += 1
        await db.commit()
    await engine.dispose()
    print(f"Seeded {len(CATALOG)} categories and {total} products.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(reset="--reset" in sys.argv)))
