import uuid

import pytest
from sqlalchemy import update

from app.core.database import async_session
from app.models.user import User
from tests.conftest import bearer


@pytest.fixture
async def admin_headers(register, login):
    await register("admin@example.com")
    async with async_session() as db:
        await db.execute(update(User).where(User.email == "admin@example.com").values(is_admin=True))
        await db.commit()
    resp = await login("admin@example.com")
    return bearer(resp.json()["access_token"])


@pytest.fixture
async def customer_headers(tokens):
    return bearer(tokens["access_token"])


@pytest.fixture
async def catalog(client, admin_headers):
    """2 categories, 4 products (one out of stock, one deactivated)."""
    shoes = (await client.post("/categories", json={"name": "Footwear"}, headers=admin_headers)).json()
    audio = (await client.post("/categories", json={"name": "Audio Gear"}, headers=admin_headers)).json()

    async def add(name, price, stock, cat, description="", **attrs):
        resp = await client.post("/products", headers=admin_headers, json={
            "name": name, "price": price, "stock_quantity": stock,
            "category_id": cat["id"], "description": description, "attributes": attrs,
        })
        assert resp.status_code == 201, resp.text
        return resp.json()

    p = {
        "runner": await add("Trail Running Shoes", "2499.00", 10, shoes, "Grippy sole for wet trails", color="Black"),
        "loafer": await add("Leather Loafers", "3999.00", 0, shoes, "Formal office shoes"),
        "earbuds": await add("Wireless Earbuds", "1499.00", 50, audio, "Noise cancelling, 30h battery"),
        "old": await add("Discontinued Headphones", "999.00", 5, audio),
    }
    await client.delete(f"/products/{p['old']['id']}", headers=admin_headers)
    return {"shoes": shoes, "audio": audio, **p}


# ---------------- permissions ----------------

async def test_customer_cannot_create_product(client, customer_headers):
    resp = await client.post("/products", headers=customer_headers, json={"name": "X", "price": "10"})
    assert resp.status_code == 403


async def test_anonymous_cannot_create_category(client):
    assert (await client.post("/categories", json={"name": "X"})).status_code == 401


async def test_anyone_can_browse(client, catalog):
    assert (await client.get("/products")).status_code == 200
    assert (await client.get("/categories")).status_code == 200


# ---------------- categories ----------------

async def test_category_slug_auto_generated(catalog):
    assert catalog["audio"]["slug"] == "audio-gear"


async def test_duplicate_category_returns_409(client, admin_headers, catalog):
    resp = await client.post("/categories", json={"name": "Footwear"}, headers=admin_headers)
    assert resp.status_code == 409


async def test_cannot_delete_category_with_products(client, admin_headers, catalog):
    resp = await client.delete(f"/categories/{catalog['shoes']['id']}", headers=admin_headers)
    assert resp.status_code == 409


async def test_delete_empty_category(client, admin_headers):
    cat = (await client.post("/categories", json={"name": "Empty"}, headers=admin_headers)).json()
    assert (await client.delete(f"/categories/{cat['id']}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"/categories/{cat['id']}")).status_code == 404


# ---------------- products: read & filters ----------------

async def test_list_hides_deactivated_products(client, catalog):
    body = (await client.get("/products")).json()
    names = {p["name"] for p in body["items"]}
    assert body["total"] == 3
    assert "Discontinued Headphones" not in names


async def test_deactivated_product_is_404_for_customers(client, catalog):
    assert (await client.get(f"/products/{catalog['old']['id']}")).status_code == 404


async def test_product_includes_category_and_stock_flag(client, catalog):
    p = (await client.get(f"/products/{catalog['loafer']['id']}")).json()
    assert p["category"]["slug"] == "footwear"
    assert p["in_stock"] is False
    assert p["price"] == "3999.00"


async def test_filter_by_category(client, catalog):
    body = (await client.get("/products", params={"category": "footwear"})).json()
    assert {p["name"] for p in body["items"]} == {"Trail Running Shoes", "Leather Loafers"}


async def test_filter_by_price_range_and_stock(client, catalog):
    body = (await client.get("/products", params={"max_price": 3000, "in_stock": True})).json()
    assert {p["name"] for p in body["items"]} == {"Trail Running Shoes", "Wireless Earbuds"}


async def test_search_matches_name_and_description(client, catalog):
    by_name = (await client.get("/products", params={"q": "earbuds"})).json()
    by_desc = (await client.get("/products", params={"q": "WET TRAILS"})).json()
    assert [p["name"] for p in by_name["items"]] == ["Wireless Earbuds"]
    assert [p["name"] for p in by_desc["items"]] == ["Trail Running Shoes"]


async def test_search_wildcards_are_literal(client, catalog):
    body = (await client.get("/products", params={"q": "%"})).json()
    assert body["total"] == 0


async def test_sort_and_pagination(client, catalog):
    page1 = (await client.get("/products", params={"sort": "price_asc", "limit": 2})).json()
    page2 = (await client.get("/products", params={"sort": "price_asc", "limit": 2, "offset": 2})).json()
    assert [p["price"] for p in page1["items"]] == ["1499.00", "2499.00"]
    assert [p["price"] for p in page2["items"]] == ["3999.00"]
    assert page1["total"] == 3


# ---------------- products: write ----------------

async def test_create_rejects_unknown_category(client, admin_headers):
    resp = await client.post("/products", headers=admin_headers,
                             json={"name": "X", "price": "10", "category_id": str(uuid.uuid4())})
    assert resp.status_code == 400


async def test_create_rejects_negative_price_and_stock(client, admin_headers):
    r1 = await client.post("/products", headers=admin_headers, json={"name": "X", "price": "-1"})
    r2 = await client.post("/products", headers=admin_headers, json={"name": "X", "price": "1", "stock_quantity": -5})
    assert r1.status_code == r2.status_code == 422


async def test_partial_update_changes_only_sent_fields(client, admin_headers, catalog):
    pid = catalog["earbuds"]["id"]
    resp = await client.patch(f"/products/{pid}", headers=admin_headers, json={"price": "1299.00"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["price"] == "1299.00"
    assert body["stock_quantity"] == 50
    assert body["description"] == "Noise cancelling, 30h battery"


async def test_update_can_move_category(client, admin_headers, catalog):
    pid = catalog["earbuds"]["id"]
    resp = await client.patch(f"/products/{pid}", headers=admin_headers, json={"category_id": catalog["shoes"]["id"]})
    assert resp.json()["category"]["slug"] == "footwear"


async def test_admin_can_reactivate_product(client, admin_headers, catalog):
    pid = catalog["old"]["id"]
    resp = await client.patch(f"/products/{pid}", headers=admin_headers, json={"is_active": True})
    assert resp.status_code == 200
    assert (await client.get(f"/products/{pid}")).status_code == 200
