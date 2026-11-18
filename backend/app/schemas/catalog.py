"""Request/response shapes for categories and products."""
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Field(ge=0, max_digits=10, decimal_places=2)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------- categories ----------------

class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=100, description="Auto-generated from name if omitted")
    description: str | None = None
    parent_id: uuid.UUID | None = None

    @field_validator("slug")
    @classmethod
    def clean_slug(cls, v: str | None) -> str | None:
        return slugify(v) if v else v


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=100)
    description: str | None = None
    parent_id: uuid.UUID | None = None

    @field_validator("slug")
    @classmethod
    def clean_slug(cls, v: str | None) -> str | None:
        return slugify(v) if v else v


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    parent_id: uuid.UUID | None


# ---------------- products ----------------

class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    price: Decimal = Money
    stock_quantity: int = Field(default=0, ge=0)
    category_id: uuid.UUID | None = None
    image_url: str | None = Field(default=None, max_length=500)
    attributes: dict[str, Any] = Field(default_factory=dict, description="e.g. brand, color, size, material")


class ProductUpdate(BaseModel):
    """Partial update: only the fields you send are changed."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    stock_quantity: int | None = Field(default=None, ge=0)
    category_id: uuid.UUID | None = None
    image_url: str | None = Field(default=None, max_length=500)
    attributes: dict[str, Any] | None = None
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    price: Decimal          # serialised as a string ("1299.00") so money never loses precision
    stock_quantity: int
    in_stock: bool
    category: CategoryOut | None
    image_url: str | None
    attributes: dict[str, Any]
    avg_rating: float
    is_active: bool
    created_at: datetime


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


ProductSort = Literal["newest", "price_asc", "price_desc", "rating", "name"]
