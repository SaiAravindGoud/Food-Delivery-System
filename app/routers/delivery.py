
import secrets
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entities import (
    Delivery,
    DeliveryOffer,
    DeliveryPartner,
    Order,
    OrderStatusHistory,
    Payment,
    User,
)
from app.schemas.delivery import (
    DeliveryLocationUpdate,
    DeliveryPartnerCreate,
    DeliveryPartnerResponse,
    DeliveryResponse,
    OTPVerificationRequest,
)
from app.services.delivery_assignment import (
    accept_delivery_offer,
    reject_delivery_offer,
    utc_now,
)
from app.websockets.manager import manager
from app.websockets.routes import load_order_tracking

router = APIRouter(tags=["Delivery Partners"])

ACTIVE_DELIVERY_STATUSES = ["Assigned", "Picked Up", "On the Way"]

ALLOWED_DELIVERY_STATUSES = {
    "Pending",
    "Assigned",
    "Picked Up",
    "On the Way",
    "Delivered",
}


async def get_partner_profile(
    db: AsyncSession,
    user_id: int,
) -> DeliveryPartner:
    result = await db.execute(
        select(DeliveryPartner).where(
            DeliveryPartner.user_id == user_id
        )
    )
    partner = result.scalar_one_or_none()

    if partner is None:
        raise HTTPException(
            status_code=404,
            detail="Delivery partner profile not found",
        )

    if not partner.is_active:
        raise HTTPException(
            status_code=403,
            detail="Delivery partner account is inactive",
        )

    return partner


async def get_partner_delivery(
    db: AsyncSession,
    delivery_id: int,
    partner_id: int,
) -> Delivery:
    result = await db.execute(
        select(Delivery)
        .options(
            selectinload(Delivery.order).selectinload(Order.items)
        )
        .where(
            Delivery.id == delivery_id,
            Delivery.delivery_partner_id == partner_id,
        )
    )
    delivery = result.scalar_one_or_none()

    if delivery is None:
        raise HTTPException(
            status_code=404,
            detail="Delivery not found for this partner",
        )

    return delivery


@router.post(
    "/delivery/partner/profile",
    response_model=DeliveryPartnerResponse,
    status_code=201,
)
async def create_partner_profile(
    payload: DeliveryPartnerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    existing = await db.execute(
        select(DeliveryPartner).where(
            DeliveryPartner.user_id == current_user.id
        )
    )

    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Delivery partner profile already exists",
        )

    partner = DeliveryPartner(
        user_id=current_user.id,
        vehicle_type=payload.vehicle_type,
        vehicle_number=payload.vehicle_number,
        is_available=True,
        is_active=True,
    )
    db.add(partner)
    await db.commit()
    await db.refresh(partner)

    return partner


@router.get(
    "/delivery/partner/profile",
    response_model=DeliveryPartnerResponse,
)
async def get_my_partner_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    return await get_partner_profile(db, current_user.id)


@router.patch(
    "/delivery/partner/location",
    response_model=DeliveryPartnerResponse,
)
async def update_partner_location(
    payload: DeliveryLocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)

    partner.current_latitude = payload.latitude
    partner.current_longitude = payload.longitude
    partner.last_location_update = utc_now()

    await db.commit()
    await db.refresh(partner)

    return partner


@router.patch(
    "/delivery/partner/availability",
    response_model=DeliveryPartnerResponse,
)
async def update_availability(
    is_available: bool,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)

    if not is_available:
        active_delivery_result = await db.execute(
            select(Delivery.id).where(
                Delivery.delivery_partner_id == partner.id,
                Delivery.status.in_(ACTIVE_DELIVERY_STATUSES),
            )
        )

        if active_delivery_result.first() is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot become unavailable while an active delivery "
                    "is assigned"
                ),
            )

    partner.is_available = is_available
    await db.commit()
    await db.refresh(partner)

    return partner


@router.get("/delivery/partner/offers")
async def list_my_delivery_offers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    """List unexpired pending offers belonging to the current partner."""
    partner = await get_partner_profile(db, current_user.id)
    now = utc_now()

    result = await db.execute(
        select(DeliveryOffer)
        .where(
            DeliveryOffer.delivery_partner_id == partner.id,
            DeliveryOffer.status == "Pending",
            DeliveryOffer.expires_at > now,
        )
        .order_by(DeliveryOffer.expires_at.asc())
    )
    offers = result.scalars().all()

    offer_items = []

    for offer in offers:
        expires_at = offer.expires_at

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        current_time = now
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        offer_items.append(
            {
                "id": offer.id,
                "order_id": offer.order_id,
                "delivery_partner_id": offer.delivery_partner_id,
                "status": offer.status,
                "offered_at": offer.offered_at,
                "expires_at": offer.expires_at,
                "seconds_remaining": max(
                    0,
                    int((expires_at - current_time).total_seconds()),
                ),
            }
        )

    return {
        "count": len(offer_items),
        "offers": offer_items,
    }


@router.post("/delivery/partner/offers/{offer_id}/accept")
async def accept_my_delivery_offer(
    offer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)

    try:
        delivery = await accept_delivery_offer(db, offer_id, partner)
        order = await db.get(Order, delivery.order_id)

        if order is None:
            raise HTTPException(
                status_code=404,
                detail="Order not found",
            )

        old_status = order.status
        order.status = "Assigned"

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                old_status=old_status,
                new_status="Assigned",
                changed_by_user_id=current_user.id,
                notes="Delivery partner accepted the delivery offer",
                created_at=utc_now(),
            )
        )

        await db.commit()
        await db.refresh(delivery)

    except ValueError as exc:
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    snapshot = await load_order_tracking(delivery.order_id)

    if snapshot:
        await manager.broadcast_order(delivery.order_id, snapshot)

    await manager.broadcast_partners(
        {
            "type": "delivery_offer_closed",
            "offer_id": offer_id,
            "order_id": delivery.order_id,
        }
    )

    return {
        "message": "Delivery offer accepted successfully",
        "delivery_id": delivery.id,
        "order_id": delivery.order_id,
        "delivery_status": delivery.status,
        "order_status": order.status,
    }


@router.post("/delivery/partner/offers/{offer_id}/reject")
async def reject_my_delivery_offer(
    offer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)

    try:
        offer = await reject_delivery_offer(db, offer_id, partner)
        await db.commit()
    except ValueError as exc:
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise

    await manager.broadcast_partners(
        {
            "type": "delivery_offer_closed",
            "offer_id": offer.id,
            "order_id": offer.order_id,
        }
    )

    return {
        "message": "Delivery offer rejected successfully",
        "offer_id": offer.id,
        "order_id": offer.order_id,
        "status": offer.status,
    }

@router.get("/delivery/partner/deliveries")
async def list_my_deliveries(
    status: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)

    filters = [Delivery.delivery_partner_id == partner.id]

    if status is not None:
        status = status.strip()

        if not status:
            raise HTTPException(
                status_code=400,
                detail="Status cannot be empty",
            )

        if status not in ALLOWED_DELIVERY_STATUSES:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Invalid delivery status",
                    "allowed_statuses": sorted(ALLOWED_DELIVERY_STATUSES),
                },
            )

        filters.append(Delivery.status == status)

    total_result = await db.execute(
        select(func.count(Delivery.id)).where(*filters)
    )
    total_count = total_result.scalar_one()

    statement = (
        select(Delivery)
        .options(
            selectinload(Delivery.order).selectinload(Order.items)
        )
        .where(*filters)
        .order_by(Delivery.id.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(statement)
    deliveries = result.scalars().all()

    return {
        "count": total_count,
        "skip": skip,
        "limit": limit,
        "deliveries": deliveries,
    }



    

@router.get("/delivery/partner/deliveries/{delivery_id}")
async def get_my_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)

    return await get_partner_delivery(
        db,
        delivery_id,
        partner.id,
    )


@router.patch(
    "/delivery/partner/deliveries/{delivery_id}/location",
    response_model=DeliveryResponse,
)
async def update_delivery_location(
    delivery_id: int,
    payload: DeliveryLocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)
    delivery = await get_partner_delivery(db, delivery_id, partner.id)

    if delivery.status not in ACTIVE_DELIVERY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Location can only be updated for an active delivery",
        )

    now = utc_now()
    delivery.current_latitude = payload.latitude
    delivery.current_longitude = payload.longitude
    partner.current_latitude = payload.latitude
    partner.current_longitude = payload.longitude
    partner.last_location_update = now

    await db.commit()
    await db.refresh(delivery)

    return delivery


@router.post(
    "/delivery/partner/deliveries/{delivery_id}/verify-pickup"
)
async def verify_pickup(
    delivery_id: int,
    payload: OTPVerificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)
    delivery = await get_partner_delivery(db, delivery_id, partner.id)

    if delivery.status != "Assigned":
        raise HTTPException(
            status_code=400,
            detail="Only assigned deliveries can be picked up",
        )

    if not delivery.pickup_otp or not secrets.compare_digest(
        delivery.pickup_otp,
        payload.otp,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid pickup OTP",
        )

    now = utc_now()
    delivery.pickup_verified = True
    delivery.picked_up_at = now
    delivery.status = "Picked Up"

    old_status = delivery.order.status
    delivery.order.status = "Picked Up"

    db.add(
        OrderStatusHistory(
            order_id=delivery.order_id,
            old_status=old_status,
            new_status="Picked Up",
            changed_by_user_id=current_user.id,
            notes="Pickup OTP verified by delivery partner",
            created_at=now,
        )
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    snapshot = await load_order_tracking(delivery.order_id)

    if snapshot:
        await manager.broadcast_order(delivery.order_id, snapshot)

    return {
        "message": "Pickup OTP verified successfully",
        "delivery_id": delivery.id,
        "order_id": delivery.order_id,
        "delivery_status": delivery.status,
        "order_status": delivery.order.status,
    }


@router.post(
    "/delivery/partner/deliveries/{delivery_id}/verify-delivery"
)
async def verify_delivery(
    delivery_id: int,
    payload: OTPVerificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("DeliveryPartner")),
):
    partner = await get_partner_profile(db, current_user.id)
    delivery = await get_partner_delivery(db, delivery_id, partner.id)

    if delivery.status not in ["Picked Up", "On the Way"]:
        raise HTTPException(
            status_code=400,
            detail="Only picked-up deliveries can be completed",
        )

    if not delivery.delivery_otp or not secrets.compare_digest(
        delivery.delivery_otp,
        payload.otp,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid delivery OTP",
        )

    now = utc_now()
    delivery.delivery_verified = True
    delivery.delivered_at = now
    delivery.status = "Delivered"
    partner.is_available = True

    old_status = delivery.order.status
    delivery.order.status = "Delivered"

    payment_status = delivery.order.payment_status

    if delivery.order.payment_method in {"COD", "Cash"}:
        payment = await db.scalar(
            select(Payment)
            .where(Payment.order_id == delivery.order_id)
            .with_for_update()
        )

        if payment is not None and payment.status == "Pending":
            payment.status = "Paid"
            payment.paid_at = now
            delivery.order.payment_status = "Paid"
            payment_status = "Paid"

    db.add(
        OrderStatusHistory(
            order_id=delivery.order_id,
            old_status=old_status,
            new_status="Delivered",
            changed_by_user_id=current_user.id,
            notes="Delivery OTP verified by delivery partner",
            created_at=now,
        )
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    snapshot = await load_order_tracking(delivery.order_id)

    if snapshot:
        await manager.broadcast_order(delivery.order_id, snapshot)

    return {
        "message": "Delivery OTP verified successfully",
        "delivery_id": delivery.id,
        "order_id": delivery.order_id,
        "delivery_status": delivery.status,
        "order_status": delivery.order.status,
        "payment_status": payment_status,
    }
