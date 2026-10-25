
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DeliveryPartnerCreate(BaseModel):
    vehicle_type: str | None = Field(default=None, max_length=50)
    vehicle_number: str | None = Field(default=None, max_length=30)


class DeliveryPartnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    vehicle_type: str | None
    vehicle_number: str | None
    is_available: bool
    is_active: bool
    current_latitude: Decimal | None
    current_longitude: Decimal | None


class DeliveryAssignmentRequest(BaseModel):
    delivery_partner_id: int = Field(gt=0)


class DeliveryLocationUpdate(BaseModel):
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)


class DeliveryStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)


class OTPVerificationRequest(BaseModel):
    otp: str = Field(pattern=r"^\d{4}$")


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    delivery_partner_id: int | None
    status: str
    pickup_verified: bool
    delivery_verified: bool
    picked_up_at: datetime | None
    delivered_at: datetime | None
    current_latitude: Decimal | None
    current_longitude: Decimal | None
