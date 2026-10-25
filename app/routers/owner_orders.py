from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entities import (
    Coupon,
    Delivery,
    DeliveryOffer,
    Order,
    OrderStatusHistory,
    Payment,
    Restaurant,
    User,
)
from app.websockets.manager import manager
from app.websockets.routes import load_order_tracking

router = APIRouter(
    prefix="/owner/orders",
    tags=["Restaurant Owner Orders"],
)

OWNER_STATUSES = {
    "Accepted",
    "Rejected",
    "Preparing",
    "Ready for Pickup",
}

ALLOWED_TRANSITIONS = {
    "Pending": {"Accepted", "Rejected"},
    "Accepted": {"Preparing", "Rejected"},
    "Preparing": {"Ready for Pickup"},
}


class OrderStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    notes: str | None = Field(default=None, max_length=500)


async def get_owner_restaurant(
    db: AsyncSession,
    owner_id: int,
) -> Restaurant:
    result = await db.execute(
        select(Restaurant).where(Restaurant.owner_id == owner_id)
    )
    restaurant = result.scalar_one_or_none()

    if restaurant is None:
        raise HTTPException(
            status_code=404,
            detail="Restaurant profile not found for this owner",
        )

    return restaurant


async def get_owned_order(
    db: AsyncSession,
    order_id: int,
    restaurant_id: int,
) -> Order:
    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.delivery),
        )
        .where(
            Order.id == order_id,
            Order.restaurant_id == restaurant_id,
        )
    )
    order = result.scalar_one_or_none()

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found for your restaurant",
        )

    return order


@router.get("")
async def list_restaurant_orders(
    order_status: str | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_owner_restaurant(db, current_user.id)

    statement = (
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.delivery),
        )
        .where(Order.restaurant_id == restaurant.id)
        .order_by(Order.placed_at.desc())
        .offset(skip)
        .limit(limit)
    )

    if order_status:
        statement = statement.where(Order.status == order_status)

    result = await db.execute(statement)
    orders = result.scalars().all()

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "count": len(orders),
        "orders": orders,
    }


@router.get("/{order_id}")
async def get_restaurant_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_owner_restaurant(db, current_user.id)
    return await get_owned_order(db, order_id, restaurant.id)


@router.patch("/{order_id}/status")
async def update_restaurant_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    new_status = payload.status.strip()

    if new_status not in OWNER_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                "Status must be Accepted, Rejected, "
                "Preparing, or Ready for Pickup"
            ),
        )

    restaurant = await get_owner_restaurant(db, current_user.id)
    order = await get_owned_order(db, order_id, restaurant.id)

    allowed_next_statuses = ALLOWED_TRANSITIONS.get(order.status, set())

    if new_status not in allowed_next_statuses:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot change order status from "
                f"{order.status} to {new_status}"
            ),
        )

    now = datetime.now(timezone.utc)
    old_status = order.status

    try:
        order.status = new_status

        if new_status == "Rejected":
            payment = await db.scalar(
                select(Payment)
                .where(Payment.order_id == order.id)
                .with_for_update()
            )

            if (
                payment is not None
                and payment.status == "Paid"
                and order.payment_method == "Online"
            ):
                payment.status = "Refunded"
                payment.paid_at = None
                order.payment_status = "Refunded"

            if order.coupon_id is not None:
                coupon = await db.scalar(
                    select(Coupon)
                    .where(Coupon.id == order.coupon_id)
                    .with_for_update()
                )

                if coupon is not None and coupon.used_count > 0:
                    coupon.used_count -= 1

            # Cancel any outstanding offers if the order is rejected.
            pending_offers = await db.scalars(
                select(DeliveryOffer).where(
                    DeliveryOffer.order_id == order.id,
                    DeliveryOffer.status == "Pending",
                )
            )

            for offer in pending_offers:
                offer.status = "Cancelled"
                offer.responded_at = now

        delivery = None

        if new_status == "Accepted":
            delivery = await db.scalar(
                select(Delivery)
                .where(Delivery.order_id == order.id)
                .with_for_update()
            )

            if delivery is None:
                delivery = Delivery(
                    order_id=order.id,
                    status="Pending",
                )
                db.add(delivery)
                await db.flush()

            elif delivery.delivery_partner_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail="This order already has an assigned delivery partner",
                )

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                old_status=old_status,
                new_status=new_status,
                changed_by_user_id=current_user.id,
                notes=payload.notes,
                created_at=now,
            )
        )

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    updated_order = await get_owned_order(db, order_id, restaurant.id)

    # Broadcast only after the database transaction succeeds.
    tracking_data = await load_order_tracking(order_id)
    await manager.broadcast_order(order_id, tracking_data)

    await manager.broadcast_restaurants(
        {
            "type": "order_status_updated",
            "order_id": order_id,
            "order_number": updated_order.order_number,
            "status": updated_order.status,
        }
    )

    if delivery is not None:
        await manager.broadcast_partners(
            {
                "type": "delivery_assignment_pending",
                "order_id": order_id,
                "delivery_id": delivery.id,
                "message": "A delivery offer will be sent to an eligible partner.",
            }
        )

    return updated_order
