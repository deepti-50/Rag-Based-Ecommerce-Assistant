"""The seed data is what the RAG assistant will read, so it must be consistent and true."""
import random

from app.scripts.seed import CATALOG, PRODUCTS_PER_CATEGORY, build_products


def _all_products(seed=42):
    rng = random.Random(seed)
    return {name: build_products(rng, spec) for name, spec in CATALOG.items()}


def test_seed_is_deterministic():
    assert _all_products() == _all_products()


def test_each_category_has_expected_count_and_valid_values():
    for name, products in _all_products().items():
        assert len(products) == PRODUCTS_PER_CATEGORY, name
        for p in products:
            assert p["price"] > 0 and p["stock_quantity"] >= 0
            assert 3.0 <= p["avg_rating"] <= 5.0


def test_descriptions_only_claim_features_of_their_own_type():
    """Guards the bug where a power bank was described with headphone features."""
    for cat_name, products in _all_products().items():
        types = CATALOG[cat_name]["types"]
        for p in products:
            ptype = next(t for t in types if f" {t.name} - " in p["name"])
            other_features = {f for t in types if t is not ptype for f in t.features} - set(ptype.features)
            assert not any(f in p["description"] for f in other_features), p["name"]
