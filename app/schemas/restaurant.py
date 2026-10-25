from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    address: str = Field(min_length=5)
    city: str = Field(min_length=2, max_length=100)

    cuisine_type: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    opening_time: time | None = None
    closing_time: time | None = None
    delivery_radius_km: Decimal = Field(default=Decimal("5.00"), gt=0)
    min_order_value: Decimal = Field(default=Decimal("0.00"), ge=0)
    avg_preparation_minutes: int = Field(default=30, ge=1)
    is_temporarily_closed: bool = False


class RestaurantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = None
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, min_length=5)
    city: str | None = Field(default=None, min_length=2, max_length=100)

    cuisine_type: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    opening_time: time | None = None
    closing_time: time | None = None
    delivery_radius_km: Decimal | None = Field(default=None, gt=0)
    min_order_value: Decimal | None = Field(default=None, ge=0)
    avg_preparation_minutes: int | None = Field(default=None, ge=1)
    is_temporarily_closed: bool | None = None

    is_active: bool | None = None
    is_open: bool | None = None


class RestaurantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    name: str
    description: str | None
    phone: str | None
    email: str | None
    address: str
    city: str

    cuisine_type: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    opening_time: time | None
    closing_time: time | None
    delivery_radius_km: Decimal
    min_order_value: Decimal
    avg_preparation_minutes: int
    is_temporarily_closed: bool

    is_active: bool
    is_open: bool
    average_rating: Decimal
    created_at: datetime


class NearbyRestaurantResponse(RestaurantResponse):
    distance_km: float
    estimated_delivery_minutes: int
