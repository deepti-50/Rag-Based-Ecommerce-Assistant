import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.security import TokenData
from app.models.product import Category, Product
from app.schemas.catalog import ProductCreate, ProductOut, ProductPage, ProductSort, ProductUpdate

router = APIRouter(prefix="/products", tags=["Catalog"])
logger = logging.getLogger("app.catalog")

_SORTS = {
    "newest": (Product.created_at.desc(),),
    "price_asc": (Product.price.asc(),),
    "price_desc": (Product.price.desc(),),
    "rating": (Product.avg_rating.desc(),),
    "name": (Product.name.asc(),),
}


def _escape_like(text: str) -> str:
    """So a search for '50%' means a literal 50%, not 'starts with 50'."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _load(db: AsyncSession, product_id: uuid.UUID, include_inactive: bool = False) -> Product:
    # Category is loaded eagerly: async SQLAlchemy can't lazy-load on attribute access.
    query = select(Product).options(selectinload(Product.category)).where(Product.id == product_id)
    if not include_inactive:
        query = query.where(Product.is_active.is_(True))
    product = await db.scalar(query)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


async def _check_category(db: AsyncSession, category_id: uuid.UUID | None) -> None:
    if category_id and not await db.get(Category, category_id):
        raise HTTPException(status_code=400, detail="category_id does not exist")


@router.get("", response_model=ProductPage)
async def list_products(
    q: str | None = Query(default=None, max_length=100, description="Search in name and description"),
    category: str | None = Query(default=None, description="Category slug"),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    in_stock: bool | None = Query(default=None, description="true = only items you can buy now"),
    sort: ProductSort = "newest",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Browse / filter the catalog. (Semantic 'ask in plain English' search comes with the RAG step.)"""
    filters = [Product.is_active.is_(True)]
    if q:
        pattern = f"%{_escape_like(q)}%"
        filters.append(or_(Product.name.ilike(pattern, escape="\\"), Product.description.ilike(pattern, escape="\\")))
    if category:
        filters.append(Product.category.has(Category.slug == category))
    if min_price is not None:
        filters.append(Product.price >= min_price)
    if max_price is not None:
        filters.append(Product.price <= max_price)
    if in_stock is True:
        filters.append(Product.stock_quantity > 0)
    elif in_stock is False:
        filters.append(Product.stock_quantity == 0)

    total = await db.scalar(select(func.count()).select_from(Product).where(*filters))
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.category))
        .where(*filters)
        .order_by(*_SORTS[sort], Product.id)  # id tie-breaker = stable pagination
        .limit(limit)
        .offset(offset)
    )
    return ProductPage(items=result.scalars().all(), total=total, limit=limit, offset=offset)


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await _load(db, product_id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    db: AsyncSession = Depends(get_db),
    admin: TokenData = Depends(require_admin),
):
    await _check_category(db, body.category_id)
    product = Product(**body.model_dump())
    db.add(product)
    await db.commit()
    logger.info("product created id=%s name=%r", product.id, product.name)
    return await _load(db, product.id)


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    admin: TokenData = Depends(require_admin),
):
    product = await _load(db, product_id, include_inactive=True)
    changes = body.model_dump(exclude_unset=True)
    if "category_id" in changes:
        await _check_category(db, changes["category_id"])
    for field, value in changes.items():
        setattr(product, field, value)
    await db.commit()
    logger.info("product updated id=%s fields=%s", product_id, ",".join(changes) or "-")
    db.expire(product)  # reload fresh values + category
    return await _load(db, product_id, include_inactive=True)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: TokenData = Depends(require_admin),
):
    """Soft delete: hides the product but keeps it, because old orders and reviews point to it."""
    product = await _load(db, product_id, include_inactive=True)
    product.is_active = False
    await db.commit()
    logger.info("product deactivated id=%s", product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
