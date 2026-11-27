"""
Build / refresh the product vector index from Postgres.

    python -m app.rag.indexer              # upsert all active products, drop removed ones
    docker compose exec api python -m app.rag.indexer

Run it after seeding, and again after catalog changes. Re-running is safe
(upsert by product id). Price/stock changes don't strictly need a re-index,
because the assistant re-reads those from Postgres; new products and changed
names/descriptions do.
"""
import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import async_session, engine
from app.core.logging_config import setup_logging
from app.models.product import Product
from app.rag.vector_store import get_collection, product_document, product_metadata

logger = logging.getLogger("app.rag")
BATCH = 100


async def index_products() -> dict:
    async with async_session() as db:
        products = (await db.scalars(
            select(Product).options(selectinload(Product.category)).where(Product.is_active.is_(True))
        )).all()

    collection = get_collection()
    ids = [str(p.id) for p in products]

    def _write() -> int:
        for i in range(0, len(products), BATCH):
            chunk = products[i:i + BATCH]
            collection.upsert(
                ids=[str(p.id) for p in chunk],
                documents=[product_document(p) for p in chunk],
                metadatas=[product_metadata(p) for p in chunk],
            )
        # Deactivated / deleted products must stop showing up in search
        stale = set(collection.get(include=[])["ids"]) - set(ids)
        if stale:
            collection.delete(ids=list(stale))
        return len(stale)

    removed = await asyncio.to_thread(_write)  # embedding is CPU work: keep it off the event loop
    logger.info("product index refreshed indexed=%d removed=%d", len(ids), removed)
    return {"indexed": len(ids), "removed": removed}


async def main() -> int:
    result = await index_products()
    await engine.dispose()
    print(f"Indexed {result['indexed']} products into Chroma ({settings.CHROMA_PERSIST_DIR}), "
          f"removed {result['removed']} stale.")
    return 0


if __name__ == "__main__":
    setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    sys.exit(asyncio.run(main()))
