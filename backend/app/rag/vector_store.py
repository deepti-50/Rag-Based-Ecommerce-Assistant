"""
The Chroma collection that holds one vector per product.

Chroma is the RETRIEVAL half of RAG: it finds products whose meaning is close
to the shopper's words ("something warm for a Delhi winter" -> hoodies, jackets),
which a SQL ILIKE search can't do.

Rule: Chroma is only used to FIND product ids. Price, stock and active status are
always re-read from Postgres (the source of truth), so a stale index can never
make the assistant quote an old price or sell something out of stock.
"""
from functools import lru_cache

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from app.core.config import settings
from app.models.product import Product


@lru_cache
def get_collection() -> Collection:
    client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION,
        # all-MiniLM-L6-v2 on ONNX: downloaded once (~80 MB) to ~/.cache/chroma, then offline
        embedding_function=DefaultEmbeddingFunction(),
        configuration={"hnsw": {"space": "cosine"}},
    )


def product_document(product: Product) -> str:
    """The text that gets embedded: everything a shopper might describe a product by."""
    attrs = product.attributes or {}
    attr_text = "; ".join(
        f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in attrs.items()
    )
    category = product.category.name if product.category else "Uncategorised"
    return f"{product.name}. Category: {category}. {product.description or ''} {attr_text}".strip()


def product_metadata(product: Product) -> dict:
    """Stored next to the vector so Chroma can pre-filter (price cap, in stock)."""
    return {
        "name": product.name,
        "category": product.category.slug if product.category else "",
        "price": float(product.price),
        "in_stock": product.in_stock,
    }
