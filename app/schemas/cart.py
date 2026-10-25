from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CartItemAdd(BaseModel):
    menu_item_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=10)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=10)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    menu_item_id: int
    quantity: int


class CartResponse(BaseModel):
    id: int
    user_id: int
    restaurant_id: int | None
    items: list[CartItemResponse]
    subtotal: Decimal
    created_at: datetime
    updated_at: datetime
