
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.database import get_db
from app.models.entities import (
    MenuCategory,
    MenuItem,
    Restaurant,
    User,
)
from app.schemas.menu import (
    MenuCategoryCreate,
    MenuCategoryResponse,
    MenuCategoryUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemUpdate,
)

router = APIRouter(tags=["Menu"])


async def get_owned_restaurant(
    restaurant_id: int,
    current_user: User,
    db: AsyncSession,
) -> Restaurant:
    restaurant = await db.get(Restaurant, restaurant_id)

    if (
        restaurant is None
        or restaurant.owner_id != current_user.id
        or not restaurant.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    return restaurant


async def get_restaurant_category(
    category_id: int,
    restaurant_id: int,
    db: AsyncSession,
) -> MenuCategory:
    category = await db.get(MenuCategory, category_id)

    if category is None or category.restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu category not found in this restaurant",
        )

    return category


async def get_customer_visible_restaurant(
    restaurant_id: int,
    current_user: User,
    db: AsyncSession,
) -> Restaurant:
    restaurant = await db.get(Restaurant, restaurant_id)

    if (
        restaurant is None
        or not restaurant.is_active
        or (
            current_user.role == "Customer"
            and (
                not restaurant.is_open
                or restaurant.is_temporarily_closed
            )
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    return restaurant


@router.post(
    "/restaurants/{restaurant_id}/categories",
    response_model=MenuCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    restaurant_id: int,
    category_data: MenuCategoryCreate,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_restaurant(restaurant_id, current_user, db)

    existing = await db.scalar(
        select(MenuCategory).where(
            MenuCategory.restaurant_id == restaurant_id,
            MenuCategory.name == category_data.name,
        )
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This category already exists for the restaurant",
        )

    category = MenuCategory(
        restaurant_id=restaurant_id,
        name=category_data.name,
        description=category_data.description,
        is_active=True,
    )

    db.add(category)

    try:
        await db.commit()
        await db.refresh(category)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This category already exists for the restaurant",
        )

    return category


@router.get(
    "/restaurants/{restaurant_id}/categories",
    response_model=list[MenuCategoryResponse],
)
async def list_categories(
    restaurant_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_customer_visible_restaurant(restaurant_id, current_user, db)

    query = select(MenuCategory).where(
        MenuCategory.restaurant_id == restaurant_id
    )

    if current_user.role == "Customer":
        query = query.where(MenuCategory.is_active.is_(True))

    result = await db.scalars(query.order_by(MenuCategory.name))
    return list(result.all())


@router.patch(
    "/restaurants/{restaurant_id}/categories/{category_id}",
    response_model=MenuCategoryResponse,
)
async def update_category(
    restaurant_id: int,
    category_id: int,
    category_data: MenuCategoryUpdate,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_restaurant(restaurant_id, current_user, db)
    category = await get_restaurant_category(category_id, restaurant_id, db)

    update_data = category_data.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] is not None:
        existing = await db.scalar(
            select(MenuCategory).where(
                MenuCategory.restaurant_id == restaurant_id,
                MenuCategory.name == update_data["name"],
                MenuCategory.id != category_id,
            )
        )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This category name is already in use",
            )

    for field, value in update_data.items():
        setattr(category, field, value)

    try:
        await db.commit()
        await db.refresh(category)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This category name is already in use",
        )

    return category


@router.post(
    "/restaurants/{restaurant_id}/items",
    response_model=MenuItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_menu_item(
    restaurant_id: int,
    item_data: MenuItemCreate,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_restaurant(restaurant_id, current_user, db)

    category = await get_restaurant_category(
        item_data.category_id,
        restaurant_id,
        db,
    )

    if not category.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add items to an inactive category",
        )

    item = MenuItem(
        restaurant_id=restaurant_id,
        category_id=item_data.category_id,
        name=item_data.name,
        description=item_data.description,
        price=item_data.price,
        packaging_charge=item_data.packaging_charge,
        image_url=item_data.image_url,
        is_vegetarian=item_data.is_vegetarian,
        is_available=item_data.is_available,
        preparation_time_minutes=item_data.preparation_time_minutes,
    )

    db.add(item)
    await db.commit()
    await db.refresh(item)

    return item


@router.get(
    "/restaurants/{restaurant_id}/items",
    response_model=list[MenuItemResponse],
)
async def list_menu_items(
    restaurant_id: int,
    category_id: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None, min_length=1, max_length=100),
    vegetarian_only: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_customer_visible_restaurant(restaurant_id, current_user, db)

    query = select(MenuItem).where(
        MenuItem.restaurant_id == restaurant_id
    )

    if current_user.role == "Customer":
        query = query.join(
            MenuCategory,
            MenuItem.category_id == MenuCategory.id,
        ).where(
            MenuItem.is_available.is_(True),
            MenuCategory.is_active.is_(True),
        )

    if category_id is not None:
        query = query.where(MenuItem.category_id == category_id)

    if search is not None:
        search_term = search.strip()

        if search_term:
            query = query.where(
                MenuItem.name.ilike(f"%{search_term}%")
            )

    if vegetarian_only:
        query = query.where(MenuItem.is_vegetarian.is_(True))

    query = query.order_by(MenuItem.name).offset(skip).limit(limit)

    result = await db.scalars(query)
    return list(result.all())


@router.get(
    "/restaurants/{restaurant_id}/menu/grouped",
)
async def get_grouped_menu(
    restaurant_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_customer_visible_restaurant(restaurant_id, current_user, db)

    category_query = select(MenuCategory).where(
        MenuCategory.restaurant_id == restaurant_id
    )

    if current_user.role == "Customer":
        category_query = category_query.where(
            MenuCategory.is_active.is_(True)
        )

    categories_result = await db.scalars(
        category_query.order_by(MenuCategory.name)
    )
    categories = list(categories_result.all())

    grouped_menu = []

    for category in categories:
        item_query = select(MenuItem).where(
            MenuItem.restaurant_id == restaurant_id,
            MenuItem.category_id == category.id,
        )

        if current_user.role == "Customer":
            item_query = item_query.where(
                MenuItem.is_available.is_(True)
            )

        items_result = await db.scalars(
            item_query.order_by(MenuItem.name)
        )

        grouped_menu.append(
            {
                "category_id": category.id,
                "category_name": category.name,
                "description": category.description,
                "items": [
                    MenuItemResponse.model_validate(item).model_dump(
                        mode="json"
                    )
                    for item in items_result.all()
                ],
            }
        )

    return {
        "restaurant_id": restaurant_id,
        "categories": grouped_menu,
    }


@router.patch(
    "/restaurants/{restaurant_id}/items/{item_id}",
    response_model=MenuItemResponse,
)
async def update_menu_item(
    restaurant_id: int,
    item_id: int,
    item_data: MenuItemUpdate,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_restaurant(restaurant_id, current_user, db)

    item = await db.get(MenuItem, item_id)

    if item is None or item.restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found in this restaurant",
        )

    update_data = item_data.model_dump(exclude_unset=True)

    if (
        "category_id" in update_data
        and update_data["category_id"] is not None
    ):
        category = await get_restaurant_category(
            update_data["category_id"],
            restaurant_id,
            db,
        )

        if not category.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot move an item to an inactive category",
            )

    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)

    return item


@router.delete(
    "/restaurants/{restaurant_id}/items/{item_id}",
    status_code=status.HTTP_200_OK,
)
async def deactivate_menu_item(
    restaurant_id: int,
    item_id: int,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_restaurant(restaurant_id, current_user, db)

    item = await db.get(MenuItem, item_id)

    if item is None or item.restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found in this restaurant",
        )

    item.is_available = False
    await db.commit()

    return {"message": "Menu item marked as unavailable"}
