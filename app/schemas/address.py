
import re
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


Latitude = Annotated[Decimal, Field(ge=-90, le=90)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180)]
PhoneNumber = Annotated[str, StringConstraints(pattern=r"^\d{10}$")]
PostalCode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class AddressCreate(BaseModel):
    label: str = Field(default="Home", min_length=1, max_length=50)
    recipient_name: str = Field(min_length=2, max_length=150)
    phone: PhoneNumber
    address_line: str = Field(min_length=5)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: PostalCode
    latitude: Latitude
    longitude: Longitude
    is_default: bool = False

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if value not in {"Home", "Work", "Other"}:
            raise ValueError("Label must be Home, Work, or Other")
        return value


class AddressUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=50)
    recipient_name: str | None = Field(
        default=None, min_length=2, max_length=150
    )
    phone: PhoneNumber | None = None
    address_line: str | None = Field(default=None, min_length=5)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    state: str | None = Field(default=None, min_length=2, max_length=100)
    postal_code: PostalCode | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if value not in {"Home", "Work", "Other"}:
            raise ValueError("Label must be Home, Work, or Other")
        return value


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    label: str
    recipient_name: str
    phone: str
    address_line: str
    city: str
    state: str
    postal_code: str
    latitude: Decimal | None
    longitude: Decimal | None
    is_default: bool
