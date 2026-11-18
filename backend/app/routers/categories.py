import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.security import TokenData
from app.models.product import Category, Product
from app.schemas.catalog import CategoryCreate, CategoryOut, CategoryUpdate, slugify

router = APIRouter(prefix="/categories", tags=["Catalog"])
logger = logging.getLogger("app.catalog")


async def _get_or_404(db: AsyncSession, category_id: uuid.UUID) -> Category:
    category = await db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


async def _commit_or_409(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A category with this name or slug already exists")


@router.get("", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category).order_by(Category.name))
    return result.scalars().all()


@router.get("/{category_id}", response_model=CategoryOut)
async def get_category(category_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(db, category_id)


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    admin: TokenData = Depends(require_admin),
):
    if body.parent_id:
        await _get_or_404(db, body.parent_id)
    category = Category(
        name=body.name,
        slug=body.slug or slugify(body.name),
        description=body.description,
        parent_id=body.parent_id,
    )
    db.add(category)
    await _commit_or_409(db)
    logger.info("category created id=%s slug=%s", category.id, category.slug)
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    admin: TokenData = Depends(require_admin),
):
    category = await _get_or_404(db, category_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("parent_id"):
        if changes["parent_id"] == category_id:
            raise HTTPException(status_code=400, detail="A category cannot be its own parent")
        await _get_or_404(db, changes["parent_id"])
    for field, value in changes.items():
        setattr(category, field, value)
    await _commit_or_409(db)
    logger.info("category updated id=%s fields=%s", category.id, ",".join(changes) or "-")
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: TokenData = Depends(require_admin),
):
    category = await _get_or_404(db, category_id)
    in_use = await db.scalar(
        select(
            exists().where(Product.category_id == category_id)
            | exists().where(Category.parent_id == category_id)
        )
    )
    if in_use:
        logger.info("category delete refused reason=has_products_or_subcategories id=%s", category_id)
        raise HTTPException(
            status_code=409,
            detail="Category still has products or subcategories. Move or delete them first.",
        )
    await db.delete(category)
    await db.commit()
    logger.info("category deleted id=%s slug=%s", category_id, category.slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
