
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entities import (
    Cart,
    CartItem,
    MenuCategory,
    MenuItem,
    Restaurant,
    User,
)
from app.schemas.cart import CartItemAdd, CartItemUpdate

router = APIRouter(prefix="/cart", tags=["Cart"])

MAX_ITEM_QUANTITY = 10


async def get_or_create_cart(
    user_id: int,
    db: AsyncSession,
) -> Cart:
    result = await db.execute(
        select(Cart)
        .options(selectinload(Cart.items))
        .where(Cart.user_id == user_id)
    )
    cart = result.scalar_one_or_none()

    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.flush()

    return cart


async def load_cart(
    user_id: int,
    db: AsyncSession,
) -> Cart:
    result = await db.execute(
        select(Cart)
        .options(selectinload(Cart.items))
        .where(Cart.user_id == user_id)
    )
    cart = result.scalar_one_or_none()

    if cart is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found",
        )

    return cart


async def get_available_menu_item(
    menu_item_id: int,
    db: AsyncSession,
) -> MenuItem | None:
    result = await db.execute(
        select(MenuItem)
        .join(
            Restaurant,
            MenuItem.restaurant_id == Restaurant.id,
        )
        .join(
            MenuCategory,
            MenuItem.category_id == MenuCategory.id,
        )
        .where(
            MenuItem.id == menu_item_id,
            MenuItem.is_available.is_(True),
            Restaurant.is_active.is_(True),
            Restaurant.is_open.is_(True),
            MenuCategory.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def build_cart_response(
    cart: Cart,
    db: AsyncSession,
) -> dict:
    result = await db.execute(
        select(CartItem, MenuItem)
        .join(
            MenuItem,
            CartItem.menu_item_id == MenuItem.id,
        )
        .where(CartItem.cart_id == cart.id)
        .order_by(CartItem.id)
    )

    rows = result.all()

    items = []
    item_subtotal = Decimal("0.00")
    packaging_total = Decimal("0.00")

    for cart_item, menu_item in rows:
        price = Decimal(str(menu_item.price))
        packaging_charge = Decimal(
            str(menu_item.packaging_charge or 0)
        )

        item_line_total = price * cart_item.quantity
        packaging_line_total = (
            packaging_charge * cart_item.quantity
        )
        line_total = item_line_total + packaging_line_total

        item_subtotal += item_line_total
        packaging_total += packaging_line_total

        items.append(
            {
                "id": cart_item.id,
                "menu_item_id": menu_item.id,
                "name": menu_item.name,
                "price": price,
                "packaging_charge": packaging_charge,
                "quantity": cart_item.quantity,
                "item_subtotal": item_line_total,
                "packaging_total": packaging_line_total,
                "line_total": line_total,
            }
        )

    subtotal = item_subtotal + packaging_total

    return {
        "id": cart.id,
        "user_id": cart.user_id,
        "restaurant_id": cart.restaurant_id,
        "items": items,
        "item_subtotal": item_subtotal,
        "packaging_total": packaging_total,
        "subtotal": subtotal,
        "created_at": cart.created_at,
        "updated_at": cart.updated_at,
    }


@router.post(
    "/items",
    status_code=status.HTTP_201_CREATED,
)
async def add_item_to_cart(
    payload: CartItemAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    menu_item = await get_available_menu_item(
        payload.menu_item_id,
        db,
    )

    if menu_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Available menu item not found",
        )

    cart = await get_or_create_cart(current_user.id, db)

    if (
        cart.restaurant_id is not None
        and cart.restaurant_id != menu_item.restaurant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Your cart has items from another restaurant. "
                "Clear the cart to continue."
            ),
        )

    existing_result = await db.execute(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.menu_item_id == menu_item.id,
        )
    )
    existing_item = existing_result.scalar_one_or_none()

    if existing_item is not None:
        new_quantity = existing_item.quantity + payload.quantity

        if new_quantity > MAX_ITEM_QUANTITY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum quantity per menu item is 10",
            )

        existing_item.quantity = new_quantity
    else:
        db.add(
            CartItem(
                cart_id=cart.id,
                menu_item_id=menu_item.id,
                quantity=payload.quantity,
            )
        )

    cart.restaurant_id = menu_item.restaurant_id

    await db.commit()

    cart = await load_cart(current_user.id, db)
    return await build_cart_response(cart, db)


@router.get("")
async def get_my_cart(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    cart = await load_cart(current_user.id, db)
    return await build_cart_response(cart, db)


@router.patch("/items/{cart_item_id}")
async def update_cart_item(
    cart_item_id: int,
    payload: CartItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    # CartItemUpdate validates quantity between 1 and 10
    # before this endpoint function is called.
    cart = await load_cart(current_user.id, db)

    result = await db.execute(
        select(CartItem).where(
            CartItem.id == cart_item_id,
            CartItem.cart_id == cart.id,
        )
    )
    cart_item = result.scalar_one_or_none()

    if cart_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found",
        )

    menu_item = await get_available_menu_item(
        cart_item.menu_item_id,
        db,
    )

    if menu_item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This menu item is no longer available",
        )

    cart_item.quantity = payload.quantity

    await db.commit()

    cart = await load_cart(current_user.id, db)
    return await build_cart_response(cart, db)


@router.delete("/items/{cart_item_id}")
async def remove_cart_item(
    cart_item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    cart = await load_cart(current_user.id, db)

    result = await db.execute(
        select(CartItem).where(
            CartItem.id == cart_item_id,
            CartItem.cart_id == cart.id,
        )
    )
    cart_item = result.scalar_one_or_none()

    if cart_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found",
        )

    await db.delete(cart_item)
    await db.flush()

    remaining_result = await db.execute(
        select(CartItem.id)
        .where(CartItem.cart_id == cart.id)
        .limit(1)
    )

    if remaining_result.scalar_one_or_none() is None:
        cart.restaurant_id = None

    await db.commit()

    return {"message": "Item removed from cart"}


@router.delete("/clear")
async def clear_cart(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    cart = await get_or_create_cart(current_user.id, db)

    await db.execute(
        delete(CartItem).where(
            CartItem.cart_id == cart.id
        )
    )

    cart.restaurant_id = None

    await db.commit()

    return {"message": "Cart cleared successfully"}
