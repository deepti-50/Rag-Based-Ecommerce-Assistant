"""
Tools the LLM can call. The docstrings ARE the instructions the model reads, so
they say when to use each tool and what the arguments mean.

Security: the model never chooses whose data it sees. The logged-in user's id comes
from the run config (set by the /chat endpoint from the JWT), not from tool
arguments, so a prompt like "show me order X of another user" can't work.
"""
import asyncio
import uuid
from decimal import Decimal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import async_session
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.review import ProductReview
from app.rag.vector_store import get_collection

DESCRIPTION_CHARS = 300  # keep tool output small: local models have short context windows


def _money(value: Decimal | None) -> str:
    return f"{value or 0:.2f}"


def _product_summary(p: Product) -> dict:
    return {
        "product_id": str(p.id),
        "name": p.name,
        "price_inr": _money(p.price),
        "in_stock": p.in_stock,
        "stock_quantity": p.stock_quantity,
        "category": p.category.name if p.category else None,
        "rating": p.avg_rating,
        "description": (p.description or "")[:DESCRIPTION_CHARS],
    }


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value).strip())
    except ValueError:
        return None


@tool
async def search_products(query: str, max_price: float | None = None, in_stock_only: bool = False) -> list[dict] | str:
    """Search the store catalog by meaning, using the shopper's own words
    (e.g. "warm hoodie for winter", "gift for someone who runs", "waterproof backpack").
    Use this for ANY question about which products exist, prices, colours, features or stock.
    max_price: optional upper price limit in INR. in_stock_only: true to hide sold-out items."""
    where: dict | None = None
    filters = []
    if max_price is not None:
        filters.append({"price": {"$lte": float(max_price)}})
    if in_stock_only:
        filters.append({"in_stock": True})
    if len(filters) == 1:
        where = filters[0]
    elif filters:
        where = {"$and": filters}

    # 1) RETRIEVE: nearest products by meaning (over-fetch: some may be filtered out below)
    hits = await asyncio.to_thread(
        get_collection().query, query_texts=[query], n_results=settings.RAG_TOP_K * 2, where=where
    )
    ids = [_parse_uuid(i) for i in hits["ids"][0]]
    ids = [i for i in ids if i]
    if not ids:
        return "No matching products found."

    # 2) GROUND: fresh price/stock from Postgres, keeping Chroma's relevance order
    async with async_session() as db:
        rows = (await db.scalars(
            select(Product).options(selectinload(Product.category))
            .where(Product.id.in_(ids), Product.is_active.is_(True))
        )).all()
    by_id = {p.id: p for p in rows}
    results = []
    for pid in ids:
        p = by_id.get(pid)
        if not p:
            continue
        if max_price is not None and p.price > Decimal(str(max_price)):
            continue
        if in_stock_only and not p.in_stock:
            continue
        results.append(_product_summary(p))
    return results[: settings.RAG_TOP_K] or "No matching products found."


@tool
async def get_product_details(product_id: str) -> dict | str:
    """Full details of ONE product (all attributes, sizes, recent reviews).
    Use after search_products, with a product_id it returned."""
    pid = _parse_uuid(product_id)
    if not pid:
        return "Invalid product_id: use a product_id returned by search_products."
    async with async_session() as db:
        p = await db.scalar(
            select(Product).options(selectinload(Product.category))
            .where(Product.id == pid, Product.is_active.is_(True))
        )
        if not p:
            return "Product not found."
        reviews = (await db.scalars(
            select(ProductReview).where(ProductReview.product_id == pid)
            .order_by(ProductReview.created_at.desc()).limit(3)
        )).all()
    return {
        **_product_summary(p),
        "description": p.description,
        "attributes": p.attributes or {},
        "recent_reviews": [{"rating": r.rating, "text": r.review_text} for r in reviews],
    }


@tool
async def get_my_orders(config: RunnableConfig, order_id: str | None = None) -> list[dict] | dict | str:
    """The logged-in customer's orders and their delivery status.
    Without order_id: their 5 most recent orders. With order_id: just that order."""
    user_id = _parse_uuid((config.get("configurable") or {}).get("user_id", ""))
    if not user_id:
        return "The customer is not signed in, so orders can't be looked up."

    query = (
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.user_id == user_id)  # only ever THIS user's orders
        .order_by(Order.created_at.desc())
    )
    if order_id:
        oid = _parse_uuid(order_id)
        if not oid:
            return "Invalid order id."
        query = query.where(Order.id == oid)
    else:
        query = query.limit(5)

    async with async_session() as db:
        orders = (await db.scalars(query)).all()
    if not orders:
        return "No order found." if order_id else "This customer has no orders yet."

    out = [{
        "order_id": str(o.id),
        "status": o.status,
        "payment_status": o.payment_status,
        "total_inr": _money(o.total_amount),
        "placed_at": o.created_at.isoformat() if o.created_at else None,
        "items": [{"name": i.product.name, "quantity": i.quantity} for i in o.items],
    } for o in orders]
    return out[0] if order_id else out


TOOLS = [search_products, get_product_details, get_my_orders]
