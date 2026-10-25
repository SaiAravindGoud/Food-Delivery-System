from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
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


router = APIRouter(tags=["Restaurant Order Handling"])

ALLOWED_ORDER_STATUSES = {
    "Pending",
    "Placed",
    "Accepted",
    "Rejected",
    "Preparing",
    "Ready for Pickup",
    "Picked Up",
    "Cancelled",
    "Delivered",
}


class AcceptOrderRequest(BaseModel):
    estimated_preparation_minutes: int = Field(ge=1, le=1440)


class RejectOrderRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


async def get_restaurant_for_owner(
    db: AsyncSession,
    owner_id: int,
) -> Restaurant:
    restaurant = await db.scalar(
        select(Restaurant).where(Restaurant.owner_id == owner_id)
    )

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
    order = await db.scalar(
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

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found for your restaurant",
        )

    return order


async def finish_order_update(
    db: AsyncSession,
    order: Order,
    old_status: str,
    new_status: str,
    owner_id: int,
    notes: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)

    order.status = new_status

    db.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=new_status,
            changed_by_user_id=owner_id,
            notes=notes,
            created_at=now,
        )
    )

    await db.commit()

    tracking_data = await load_order_tracking(order.id)
    await manager.broadcast_order(order.id, tracking_data)

    await manager.broadcast_restaurants(
        {
            "type": "order_status_updated",
            "order_id": order.id,
            "order_number": order.order_number,
            "status": new_status,
        }
    )


@router.get("/restaurants/me/orders")
async def list_my_restaurant_orders(
    status: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_restaurant_for_owner(db, current_user.id)

    filters = [Order.restaurant_id == restaurant.id]

    if status is not None:
        status = status.strip()

        if not status:
            raise HTTPException(
                status_code=400,
                detail="Status cannot be empty",
            )

        if status not in ALLOWED_ORDER_STATUSES:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Invalid order status",
                    "allowed_statuses": sorted(ALLOWED_ORDER_STATUSES),
                },
            )

        filters.append(Order.status == status)

    total_result = await db.execute(
        select(func.count(Order.id)).where(*filters)
    )
    total_count = total_result.scalar_one()

    statement = (
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.delivery),
        )
        .where(*filters)
        .order_by(Order.placed_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(statement)
    orders = result.scalars().all()

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "count": total_count,
        "skip": skip,
        "limit": limit,
        "orders": orders,
    }


@router.put("/orders/{order_id}/accept")
async def accept_restaurant_order(
    order_id: int,
    payload: AcceptOrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_restaurant_for_owner(db, current_user.id)
    order = await get_owned_order(db, order_id, restaurant.id)

    if order.status not in {"Pending", "Placed"}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot accept an order with status {order.status}",
        )

    now = datetime.now(timezone.utc)
    old_status = order.status
    delivery = None

    try:
        order.estimated_preparation_minutes = (
            payload.estimated_preparation_minutes
        )
        order.status = "Accepted"

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
                new_status="Accepted",
                changed_by_user_id=current_user.id,
                notes=(
                    "Estimated preparation time: "
                    f"{payload.estimated_preparation_minutes} minutes"
                ),
                created_at=now,
            )
        )

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    updated_order = await get_owned_order(db, order_id, restaurant.id)

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
                "message": (
                    "A delivery offer will be sent to an eligible partner."
                ),
            }
        )

    return updated_order


@router.put("/orders/{order_id}/reject")
async def reject_restaurant_order(
    order_id: int,
    payload: RejectOrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_restaurant_for_owner(db, current_user.id)
    order = await get_owned_order(db, order_id, restaurant.id)

    if order.status not in {"Pending", "Placed", "Accepted"}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject an order with status {order.status}",
        )

    now = datetime.now(timezone.utc)
    old_status = order.status

    try:
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

        pending_offers = await db.scalars(
            select(DeliveryOffer).where(
                DeliveryOffer.order_id == order.id,
                DeliveryOffer.status == "Pending",
            )
        )

        for offer in pending_offers:
            offer.status = "Cancelled"
            offer.responded_at = now

        order.status = "Rejected"

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                old_status=old_status,
                new_status="Rejected",
                changed_by_user_id=current_user.id,
                notes=payload.reason.strip(),
                created_at=now,
            )
        )

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    updated_order = await get_owned_order(db, order_id, restaurant.id)

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

    return updated_order


@router.put("/orders/{order_id}/preparing")
async def mark_order_preparing(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_restaurant_for_owner(db, current_user.id)
    order = await get_owned_order(db, order_id, restaurant.id)

    if order.status != "Accepted":
        raise HTTPException(
            status_code=400,
            detail="Only accepted orders can be marked as preparing",
        )

    try:
        await finish_order_update(
            db=db,
            order=order,
            old_status=order.status,
            new_status="Preparing",
            owner_id=current_user.id,
        )
    except Exception:
        await db.rollback()
        raise

    return await get_owned_order(db, order_id, restaurant.id)


@router.put("/orders/{order_id}/ready")
async def mark_order_ready(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("RestaurantOwner")),
):
    restaurant = await get_restaurant_for_owner(db, current_user.id)
    order = await get_owned_order(db, order_id, restaurant.id)

    if order.status != "Preparing":
        raise HTTPException(
            status_code=400,
            detail="Only preparing orders can be marked ready for pickup",
        )

    try:
        await finish_order_update(
            db=db,
            order=order,
            old_status=order.status,
            new_status="Ready for Pickup",
            owner_id=current_user.id,
        )
    except Exception:
        await db.rollback()
        raise

    return await get_owned_order(db, order_id, restaurant.id)
