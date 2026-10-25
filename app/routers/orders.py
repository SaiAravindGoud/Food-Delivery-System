
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entities import (
    Address,
    Cart,
    CartItem,
    Coupon,
    Delivery,
    MenuCategory,
    MenuItem,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    Restaurant,
    User,
)
from app.schemas.orders import OrderCreate, OrderResponse

router = APIRouter(prefix="/orders", tags=["Orders"])

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
ALLOWED_PAYMENT_METHODS = {"COD", "ONLINE"}

ALLOWED_ORDER_STATUSES = {
    "Pending",
    "Placed",
    "Accepted",
    "Rejected",
    "Preparing",
    "Ready for Pickup",
    "Cancelled",
    "Delivered",
}


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def make_order_number() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"ORD-{timestamp}-{secrets.token_hex(3).upper()}"


def make_transaction_reference() -> str:
    return f"MOCK-{secrets.token_hex(8).upper()}"


async def load_order(
    order_id: int,
    db: AsyncSession,
) -> Order | None:
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    payment_method = payload.payment_method.strip().upper()

    if payment_method not in ALLOWED_PAYMENT_METHODS:
        raise HTTPException(
            status_code=422,
            detail="Payment method must be COD or Online",
        )

    stored_payment_method = (
        "COD" if payment_method == "COD" else "Online"
    )

    try:
        address_result = await db.execute(
            select(Address).where(
                Address.id == payload.delivery_address_id,
                Address.user_id == current_user.id,
            )
        )
        address = address_result.scalar_one_or_none()

        if address is None:
            raise HTTPException(
                status_code=404,
                detail="Delivery address not found",
            )

        cart_result = await db.execute(
            select(Cart)
            .options(selectinload(Cart.items))
            .where(Cart.user_id == current_user.id)
        )
        cart = cart_result.scalar_one_or_none()

        if cart is None or not cart.items or cart.restaurant_id is None:
            raise HTTPException(
                status_code=400,
                detail="Your cart is empty",
            )

        restaurant_result = await db.execute(
            select(Restaurant).where(
                Restaurant.id == cart.restaurant_id
            )
        )
        restaurant = restaurant_result.scalar_one_or_none()

        if restaurant is None or not restaurant.is_active:
            raise HTTPException(
                status_code=400,
                detail="Restaurant is unavailable",
            )

        if not restaurant.is_open or restaurant.is_temporarily_closed:
            raise HTTPException(
                status_code=400,
                detail="Restaurant is currently closed",
            )

        food_subtotal = ZERO
        packaging_total = ZERO
        order_items_data = []

        for cart_item in cart.items:
            item_result = await db.execute(
                select(MenuItem, MenuCategory)
                .join(
                    MenuCategory,
                    MenuItem.category_id == MenuCategory.id,
                )
                .where(
                    MenuItem.id == cart_item.menu_item_id,
                    MenuItem.restaurant_id == restaurant.id,
                )
            )
            row = item_result.first()

            if row is None:
                raise HTTPException(
                    status_code=400,
                    detail="A cart item is no longer available",
                )

            menu_item, category = row

            if not menu_item.is_available or not category.is_active:
                raise HTTPException(
                    status_code=400,
                    detail=f"Menu item '{menu_item.name}' is unavailable",
                )

            if cart_item.quantity < 1 or cart_item.quantity > 10:
                raise HTTPException(
                    status_code=400,
                    detail="Each item quantity must be between 1 and 10",
                )

            price = Decimal(str(menu_item.price))
            packaging_charge = Decimal(
                str(menu_item.packaging_charge or 0)
            )

            item_food_total = money(price * cart_item.quantity)
            item_packaging_total = money(
                packaging_charge * cart_item.quantity
            )

            food_subtotal += item_food_total
            packaging_total += item_packaging_total

            order_items_data.append(
                {
                    "menu_item_id": menu_item.id,
                    "item_name": menu_item.name,
                    "quantity": cart_item.quantity,
                    "unit_price": price,
                    "subtotal": item_food_total,
                }
            )

        food_subtotal = money(food_subtotal)
        packaging_total = money(packaging_total)
        subtotal = money(food_subtotal + packaging_total)

        minimum_order = Decimal(
            str(restaurant.min_order_value or 0)
        )

        if food_subtotal < minimum_order:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Minimum food order amount is "
                    f"{minimum_order:.2f}"
                ),
            )

        coupon = None
        discount_amount = ZERO

        if payload.coupon_code:
            code = payload.coupon_code.strip().upper()

            coupon_result = await db.execute(
                select(Coupon)
                .where(Coupon.code == code)
                .with_for_update()
            )
            coupon = coupon_result.scalar_one_or_none()

            if coupon is None or not coupon.is_active:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or inactive coupon",
                )

            now = datetime.now(timezone.utc)

            if (
                coupon.starts_at is not None
                and now < as_utc(coupon.starts_at)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Coupon is not active yet",
                )

            if (
                coupon.expires_at is not None
                and now >= as_utc(coupon.expires_at)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Coupon has expired",
                )

            if (
                coupon.usage_limit is not None
                and coupon.used_count >= coupon.usage_limit
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Coupon usage limit reached",
                )

            minimum_coupon_order = Decimal(
                str(coupon.minimum_order_amount or 0)
            )

            if food_subtotal < minimum_coupon_order:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Order does not meet the coupon minimum amount"
                    ),
                )

            discount_type = coupon.discount_type.lower()
            discount_value = Decimal(str(coupon.discount_value))

            if discount_type in {"percentage", "percent"}:
                discount_amount = money(
                    food_subtotal * discount_value / Decimal("100")
                )
            elif discount_type in {"fixed", "flat", "amount"}:
                discount_amount = money(discount_value)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported coupon discount type",
                )

            if coupon.maximum_discount is not None:
                discount_amount = min(
                    discount_amount,
                    Decimal(str(coupon.maximum_discount)),
                )

            # Never discount packaging charges.
            discount_amount = min(
                max(discount_amount, ZERO),
                food_subtotal,
            )

        delivery_fee = ZERO
        total_amount = money(
            max(ZERO, subtotal + delivery_fee - discount_amount)
        )

        now = datetime.now(timezone.utc)
        preparation_minutes = restaurant.avg_preparation_minutes or 25

        payment_status = (
            "Paid" if payment_method == "ONLINE" else "Pending"
        )

        order = Order(
            order_number=make_order_number(),
            customer_id=current_user.id,
            restaurant_id=restaurant.id,
            delivery_address_id=address.id,
            coupon_id=coupon.id if coupon else None,
            status="Pending",
            payment_method=stored_payment_method,
            payment_status=payment_status,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            discount_amount=discount_amount,
            total_amount=total_amount,
            special_instructions=payload.special_instructions,
            estimated_delivery_at=now + timedelta(
                minutes=preparation_minutes
            ),
            placed_at=now,
        )

        db.add(order)
        await db.flush()

        # Create the pending delivery record in the same transaction.
        db.add(
            Delivery(
                order_id=order.id,
                status="Pending",
            )
        )

        for item_data in order_items_data:
            db.add(
                OrderItem(
                    order_id=order.id,
                    menu_item_id=item_data["menu_item_id"],
                    item_name=item_data["item_name"],
                    quantity=item_data["quantity"],
                    unit_price=item_data["unit_price"],
                    subtotal=item_data["subtotal"],
                )
            )

        db.add(
            Payment(
                order_id=order.id,
                amount=total_amount,
                payment_method=stored_payment_method,
                status=payment_status,
                transaction_reference=(
                    make_transaction_reference()
                    if payment_method == "ONLINE"
                    else None
                ),
                paid_at=now if payment_method == "ONLINE" else None,
            )
        )

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                old_status=None,
                new_status="Pending",
                changed_by_user_id=current_user.id,
                notes="Order placed by customer",
            )
        )

        if coupon is not None:
            coupon.used_count += 1

        cart.items.clear()
        cart.restaurant_id = None

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    created_order = await load_order(order.id, db)

    if created_order is None:
        raise HTTPException(
            status_code=500,
            detail="Order was created but could not be loaded",
        )

    return created_order


@router.get("", response_model=list[OrderResponse])
async def list_my_orders(
    status: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    statement = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.customer_id == current_user.id)
    )

    if status is not None:
        status = status.strip()

        if status not in ALLOWED_ORDER_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid order status. Allowed values: "
                    + ", ".join(sorted(ALLOWED_ORDER_STATUSES))
                ),
            )

        statement = statement.where(Order.status == status)

    statement = (
        statement
        .order_by(Order.placed_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(statement)
    return result.scalars().all()


@router.get("/{order_id}", response_model=OrderResponse)
async def get_my_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    order = await load_order(order_id, db)

    if order is None or order.customer_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    return order


@router.put("/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    try:
        order_result = await db.execute(
            select(Order)
            .where(
                Order.id == order_id,
                Order.customer_id == current_user.id,
            )
            .with_for_update()
        )
        order = order_result.scalar_one_or_none()

        if order is None:
            raise HTTPException(
                status_code=404,
                detail="Order not found",
            )

        # This project uses Pending as the initial order status.
        if order.status not in {"Pending", "Placed"}:
            raise HTTPException(
                status_code=400,
                detail=(
                    "An order can only be cancelled before "
                    "the restaurant accepts it"
                ),
            )

        old_status = order.status
        order.status = "Cancelled"

        payment_result = await db.execute(
            select(Payment)
            .where(Payment.order_id == order.id)
            .with_for_update()
        )
        payment = payment_result.scalar_one_or_none()

        if (
            order.payment_method.lower() == "online"
            and payment is not None
            and payment.status == "Paid"
        ):
            payment.status = "Refunded"
            order.payment_status = "Refunded"

        if order.coupon_id is not None:
            coupon_result = await db.execute(
                select(Coupon)
                .where(Coupon.id == order.coupon_id)
                .with_for_update()
            )
            coupon = coupon_result.scalar_one_or_none()

            if coupon is not None and coupon.used_count > 0:
                coupon.used_count -= 1

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                old_status=old_status,
                new_status="Cancelled",
                changed_by_user_id=current_user.id,
                notes="Cancelled by customer",
            )
        )

        await db.commit()

        return {
            "message": "Order cancelled successfully",
            "order_id": order.id,
            "status": order.status,
            "payment_status": order.payment_status,
        }

    except Exception:
        await db.rollback()
        raise


@router.post("/{order_id}/reorder")
async def reorder(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    try:
        previous_result = await db.execute(
            select(Order)
            .options(selectinload(Order.items))
            .where(
                Order.id == order_id,
                Order.customer_id == current_user.id,
            )
        )
        previous_order = previous_result.scalar_one_or_none()

        if previous_order is None:
            raise HTTPException(
                status_code=404,
                detail="Previous order not found",
            )

        cart_result = await db.execute(
            select(Cart)
            .options(selectinload(Cart.items))
            .where(Cart.user_id == current_user.id)
        )
        cart = cart_result.scalar_one_or_none()

        if cart is None:
            cart = Cart(user_id=current_user.id)
            db.add(cart)
            await db.flush()

        if (
            cart.items
            and cart.restaurant_id is not None
            and cart.restaurant_id != previous_order.restaurant_id
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Your cart contains items from another restaurant. "
                    "Clear your cart first."
                ),
            )

        restaurant = await db.get(
            Restaurant,
            previous_order.restaurant_id,
        )

        if (
            restaurant is None
            or not restaurant.is_active
            or not restaurant.is_open
            or restaurant.is_temporarily_closed
        ):
            raise HTTPException(
                status_code=400,
                detail="The previous order's restaurant is unavailable",
            )

        added_items = []
        skipped_items = []

        cart.restaurant_id = previous_order.restaurant_id

        for old_item in previous_order.items:
            item_result = await db.execute(
                select(MenuItem, MenuCategory)
                .join(
                    MenuCategory,
                    MenuItem.category_id == MenuCategory.id,
                )
                .where(
                    MenuItem.id == old_item.menu_item_id,
                    MenuItem.restaurant_id == previous_order.restaurant_id,
                    MenuItem.is_available.is_(True),
                    MenuCategory.is_active.is_(True),
                )
            )
            row = item_result.first()

            if row is None:
                skipped_items.append(old_item.item_name)
                continue

            menu_item, _category = row

            existing_item = next(
                (
                    cart_item
                    for cart_item in cart.items
                    if cart_item.menu_item_id == menu_item.id
                ),
                None,
            )

            if existing_item is not None:
                existing_item.quantity = min(
                    10,
                    existing_item.quantity + old_item.quantity,
                )
            else:
                new_cart_item = CartItem(
                    cart_id=cart.id,
                    menu_item_id=menu_item.id,
                    quantity=min(10, old_item.quantity),
                )
                cart.items.append(new_cart_item)

            added_items.append(menu_item.name)

        if not added_items:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No items from the previous order are "
                    "currently available"
                ),
            )

        await db.commit()

        return {
            "message": "Available items added to your cart",
            "previous_order_id": previous_order.id,
            "restaurant_id": previous_order.restaurant_id,
            "added_items": added_items,
            "skipped_items": skipped_items,
            "coupon_reapplied": False,
        }

    except Exception:
        await db.rollback()
        raise
