
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MenuCategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = None


class MenuCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class MenuCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_id: int
    name: str
    description: str | None
    is_active: bool


class MenuItemCreate(BaseModel):
    category_id: int
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    packaging_charge: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=10,
        decimal_places=2,
    )
    image_url: str | None = Field(default=None, max_length=500)
    is_vegetarian: bool = False
    is_available: bool = True
    preparation_time_minutes: int = Field(default=20, ge=1, le=300)


class MenuItemUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = None
    price: Decimal | None = Field(
        default=None, ge=0, max_digits=10, decimal_places=2
    )
    packaging_charge: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=10,
        decimal_places=2,
    )
    image_url: str | None = Field(default=None, max_length=500)
    is_vegetarian: bool | None = None
    is_available: bool | None = None
    preparation_time_minutes: int | None = Field(default=None, ge=1, le=300)


class MenuItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_id: int
    category_id: int
    name: str
    description: str | None
    price: Decimal
    packaging_charge: Decimal
    image_url: str | None
    is_vegetarian: bool
    is_available: bool
    preparation_time_minutes: int
    created_at: datetime
