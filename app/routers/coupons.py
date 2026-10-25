
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entities import Coupon, User


router = APIRouter(prefix="/coupons", tags=["Coupons"])


class CouponCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    discount_type: str = "percentage"
    discount_value: Decimal = Field(gt=0)
    minimum_order_amount: Decimal = Field(default=Decimal("0"), ge=0)
    maximum_discount: Decimal | None = Field(default=None, gt=0)
    usage_limit: int | None = Field(default=None, gt=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True


class CouponUpdate(BaseModel):
    discount_type: str | None = None
    discount_value: Decimal | None = Field(default=None, gt=0)
    minimum_order_amount: Decimal | None = Field(default=None, ge=0)
    maximum_discount: Decimal | None = Field(default=None, gt=0)
    usage_limit: int | None = Field(default=None, gt=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool | None = None


def coupon_response(coupon: Coupon) -> dict:
    return {
        "id": coupon.id,
        "code": coupon.code,
        "discount_type": coupon.discount_type,
        "discount_value": coupon.discount_value,
        "minimum_order_amount": coupon.minimum_order_amount,
        "maximum_discount": coupon.maximum_discount,
        "usage_limit": coupon.usage_limit,
        "used_count": coupon.used_count,
        "starts_at": coupon.starts_at,
        "expires_at": coupon.expires_at,
        "is_active": coupon.is_active,
    }


@router.get("")
async def list_coupons(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    result = await db.execute(select(Coupon).order_by(Coupon.id.desc()))
    return [coupon_response(coupon) for coupon in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_coupon(
    payload: CouponCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Coupon code cannot be empty")

    existing = await db.scalar(select(Coupon).where(Coupon.code == code))
    if existing:
        raise HTTPException(status_code=409, detail="Coupon code already exists")

    discount_type = payload.discount_type.strip().lower()
    if discount_type not in {"percentage", "fixed"}:
        raise HTTPException(
            status_code=400,
            detail="discount_type must be 'percentage' or 'fixed'",
        )

    if discount_type == "percentage" and payload.discount_value > 100:
        raise HTTPException(
            status_code=400,
            detail="Percentage discount cannot exceed 100",
        )

    if (
        payload.starts_at
        and payload.expires_at
        and payload.expires_at <= payload.starts_at
    ):
        raise HTTPException(
            status_code=400,
            detail="expires_at must be later than starts_at",
        )

    coupon = Coupon(
        code=code,
        discount_type=discount_type,
        discount_value=payload.discount_value,
        minimum_order_amount=payload.minimum_order_amount,
        maximum_discount=payload.maximum_discount,
        usage_limit=payload.usage_limit,
        starts_at=payload.starts_at,
        expires_at=payload.expires_at,
        is_active=payload.is_active,
        used_count=0,
    )
    db.add(coupon)

    try:
        await db.commit()
        await db.refresh(coupon)
    except Exception:
        await db.rollback()
        raise

    return coupon_response(coupon)


@router.get("/{coupon_id}")
async def get_coupon(
    coupon_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    coupon = await db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return coupon_response(coupon)


@router.patch("/{coupon_id}")
async def update_coupon(
    coupon_id: int,
    payload: CouponUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    coupon = await db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")

    changes = payload.model_dump(exclude_unset=True)

    discount_type = changes.get("discount_type", coupon.discount_type)
    if discount_type is not None:
        discount_type = discount_type.strip().lower()
        if discount_type not in {"percentage", "fixed"}:
            raise HTTPException(
                status_code=400,
                detail="discount_type must be 'percentage' or 'fixed'",
            )
        changes["discount_type"] = discount_type

    discount_value = changes.get("discount_value", coupon.discount_value)
    if discount_type == "percentage" and discount_value is not None:
        if discount_value > 100:
            raise HTTPException(
                status_code=400,
                detail="Percentage discount cannot exceed 100",
            )

    starts_at = changes.get("starts_at", coupon.starts_at)
    expires_at = changes.get("expires_at", coupon.expires_at)
    if starts_at and expires_at and expires_at <= starts_at:
        raise HTTPException(
            status_code=400,
            detail="expires_at must be later than starts_at",
        )

    if "code" in changes:
        changes.pop("code")

    for field, value in changes.items():
        setattr(coupon, field, value)

    try:
        await db.commit()
        await db.refresh(coupon)
    except Exception:
        await db.rollback()
        raise

    return coupon_response(coupon)


@router.delete("/{coupon_id}")
async def deactivate_coupon(
    coupon_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    coupon = await db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")

    coupon.is_active = False

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {"message": "Coupon deactivated successfully", "coupon_id": coupon.id}
