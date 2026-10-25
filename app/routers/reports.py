
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.entities import (
    Delivery,
    DeliveryPartner,
    Order,
    OrderItem,
    Payment,
    Restaurant,
    Review,
    User,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


def require_role(current_user: User, allowed_roles: set[str]) -> None:
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this report",
        )


def money(value) -> float:
    return round(float(value or 0), 2)


def date_range(
    start_date: date | None,
    end_date: date | None,
) -> tuple[datetime, datetime]:
    start = start_date or date.today().replace(day=1)
    end = end_date or date.today()

    if start > end:
        raise HTTPException(
            status_code=400,
            detail="start_date must be on or before end_date",
        )

    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end + timedelta(days=1), time.min)
    return start_dt, end_dt


@router.get("/admin-dashboard")
async def admin_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_role(current_user, {"Admin"})

    today = date.today()
    start_dt, end_dt = date_range(today, today)

    order_counts = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(
                func.sum(
                    case((Order.status == "Delivered", 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((Order.status == "Cancelled", 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((Order.status == "Rejected", 1), else_=0)
                ),
                0,
            ),
        ).where(
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
        )
    )
    total_orders, delivered_orders, cancelled_orders, rejected_orders = (
        order_counts.one()
    )

    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .join(Order, Payment.order_id == Order.id)
        .where(
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
            Payment.status == "Paid",
        )
    )

    customer_count = await db.scalar(
        select(func.count(User.id)).where(User.role == "Customer")
    )
    restaurant_count = await db.scalar(
        select(func.count(Restaurant.id)).where(
            Restaurant.is_active.is_(True)
        )
    )
    online_partners = await db.scalar(
        select(func.count(DeliveryPartner.id)).where(
            DeliveryPartner.is_active.is_(True),
            DeliveryPartner.is_available.is_(True),
        )
    )

    return {
        "report_date": today.isoformat(),
        "orders": {
            "total": total_orders,
            "delivered": delivered_orders,
            "cancelled": cancelled_orders,
            "rejected": rejected_orders,
        },
        "paid_revenue": money(revenue_result.scalar_one()),
        "customers": customer_count or 0,
        "active_restaurants": restaurant_count or 0,
        "available_delivery_partners": online_partners or 0,
    }


@router.get("/order-status-summary")
async def order_status_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_role(current_user, {"Admin"})

    start_dt, end_dt = date_range(start_date, end_date)

    result = await db.execute(
        select(Order.status, func.count(Order.id))
        .where(
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
        )
        .group_by(Order.status)
        .order_by(Order.status)
    )

    rows = result.all()
    return {
        "start_date": start_dt.date().isoformat(),
        "end_date": (end_dt.date() - timedelta(days=1)).isoformat(),
        "total_orders": sum(count for _, count in rows),
        "statuses": [
            {"status": status, "count": count}
            for status, count in rows
        ],
    }


@router.get("/top-restaurants")
async def top_restaurants(
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_role(current_user, {"Admin"})

    start_dt, end_dt = date_range(start_date, end_date)

    result = await db.execute(
        select(
            Restaurant.id,
            Restaurant.name,
            func.count(Order.id).label("order_count"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Payment.status == "Paid",
                            Payment.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("paid_revenue"),
            Restaurant.average_rating,
        )
        .join(Order, Order.restaurant_id == Restaurant.id)
        .outerjoin(Payment, Payment.order_id == Order.id)
        .where(
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
        )
        .group_by(
            Restaurant.id,
            Restaurant.name,
            Restaurant.average_rating,
        )
        .order_by(func.count(Order.id).desc(), Restaurant.name)
        .limit(limit)
    )

    return {
        "start_date": start_dt.date().isoformat(),
        "end_date": (end_dt.date() - timedelta(days=1)).isoformat(),
        "restaurants": [
            {
                "restaurant_id": row.id,
                "restaurant_name": row.name,
                "order_count": row.order_count,
                "paid_revenue": money(row.paid_revenue),
                "average_rating": money(row.average_rating),
            }
            for row in result.all()
        ],
    }


@router.get("/restaurant-sales")
async def restaurant_sales(
    start_date: date | None = None,
    end_date: date | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_role(current_user, {"RestaurantOwner"})

    restaurant = await db.scalar(
        select(Restaurant).where(
            Restaurant.owner_id == current_user.id
        )
    )
    if restaurant is None:
        raise HTTPException(
            status_code=404,
            detail="Restaurant profile not found",
        )

    start_dt, end_dt = date_range(start_date, end_date)

    order_result = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Payment.status == "Paid",
                            Payment.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .outerjoin(Payment, Payment.order_id == Order.id)
        .where(
            Order.restaurant_id == restaurant.id,
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
        )
    )
    total_orders, paid_revenue = order_result.one()

    daily_result = await db.execute(
        select(
            func.date(Order.placed_at).label("order_date"),
            func.count(Order.id).label("order_count"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Payment.status == "Paid",
                            Payment.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("paid_revenue"),
        )
        .outerjoin(Payment, Payment.order_id == Order.id)
        .where(
            Order.restaurant_id == restaurant.id,
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
        )
        .group_by(func.date(Order.placed_at))
        .order_by(func.date(Order.placed_at))
    )

    best_sellers = await db.execute(
        select(
            OrderItem.item_name,
            func.sum(OrderItem.quantity).label("quantity_sold"),
            func.sum(OrderItem.subtotal).label("item_sales"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.restaurant_id == restaurant.id,
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
            Order.status == "Delivered",
        )
        .group_by(OrderItem.item_name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(10)
    )

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "start_date": start_dt.date().isoformat(),
        "end_date": (end_dt.date() - timedelta(days=1)).isoformat(),
        "total_orders": total_orders,
        "paid_revenue": money(paid_revenue),
        "daily_sales": [
            {
                "date": str(row.order_date),
                "order_count": row.order_count,
                "paid_revenue": money(row.paid_revenue),
            }
            for row in daily_result.all()
        ],
        "top_selling_items": [
            {
                "item_name": row.item_name,
                "quantity_sold": int(row.quantity_sold or 0),
                "item_sales": money(row.item_sales),
            }
            for row in best_sellers.all()
        ],
    }


@router.get("/partner-summary")
async def partner_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_role(current_user, {"DeliveryPartner"})

    partner = await db.scalar(
        select(DeliveryPartner).where(
            DeliveryPartner.user_id == current_user.id
        )
    )
    if partner is None:
        raise HTTPException(
            status_code=404,
            detail="Delivery partner profile not found",
        )

    start_dt, end_dt = date_range(start_date, end_date)

    result = await db.execute(
        select(
            func.count(Delivery.id),
            func.avg(
                func.timestampdiff(
                    func.literal_column("MINUTE"),
                    Delivery.picked_up_at,
                    Delivery.delivered_at,
                )
            ),
        )
        .join(Order, Delivery.order_id == Order.id)
        .where(
            Delivery.delivery_partner_id == partner.id,
            Delivery.status == "Delivered",
            Delivery.delivered_at >= start_dt,
            Delivery.delivered_at < end_dt,
        )
    )

    completed_count, average_delivery_minutes = result.one()

    return {
        "delivery_partner_id": partner.id,
        "start_date": start_dt.date().isoformat(),
        "end_date": (end_dt.date() - timedelta(days=1)).isoformat(),
        "completed_deliveries": completed_count or 0,
        "average_delivery_minutes": (
            round(float(average_delivery_minutes), 2)
            if average_delivery_minutes is not None
            else None
        ),
        "distance_and_earnings": (
            "Not available: the current database models do not store "
            "delivery distance or partner earnings."
        ),
    }


@router.get("/coupon-usage")
async def coupon_usage(
    start_date: date | None = None,
    end_date: date | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_role(current_user, {"Admin"})

    start_dt, end_dt = date_range(start_date, end_date)

    result = await db.execute(
        select(
            Order.coupon_id,
            func.count(Order.id).label("orders_using_coupon"),
            func.coalesce(func.sum(Order.discount_amount), 0).label(
                "total_discount"
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Payment.status == "Paid",
                            Payment.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("paid_revenue"),
        )
        .outerjoin(Payment, Payment.order_id == Order.id)
        .where(
            Order.coupon_id.is_not(None),
            Order.placed_at >= start_dt,
            Order.placed_at < end_dt,
        )
        .group_by(Order.coupon_id)
        .order_by(func.count(Order.id).desc())
    )

    rows = result.all()
    return {
        "start_date": start_dt.date().isoformat(),
        "end_date": (end_dt.date() - timedelta(days=1)).isoformat(),
        "coupons": [
            {
                "coupon_id": row.coupon_id,
                "orders_using_coupon": row.orders_using_coupon,
                "total_discount": money(row.total_discount),
                "paid_revenue": money(row.paid_revenue),
            }
            for row in rows
        ],
    }
