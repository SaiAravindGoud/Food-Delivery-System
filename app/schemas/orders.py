
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    delivery_address_id: int = Field(gt=0)
    payment_method: str = Field(
        default="Cash",
        min_length=1,
        max_length=30,
    )
    coupon_code: str | None = Field(
        default=None,
        max_length=50,
    )
    special_instructions: str | None = Field(
        default=None,
        max_length=1000,
    )


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    menu_item_id: int
    item_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class OrderResponse(BaseModel):
    id: int
    order_number: str
    customer_id: int
    restaurant_id: int
    delivery_address_id: int
    coupon_id: int | None
    status: str
    payment_method: str
    payment_status: str
    subtotal: Decimal
    delivery_fee: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    special_instructions: str | None
    estimated_delivery_at: datetime | None
    placed_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse]
