from datetime import datetime
from decimal import Decimal
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.database import get_db
from app.models.entities import Restaurant, User
from app.schemas.restaurant import (
    NearbyRestaurantResponse,
    RestaurantCreate,
    RestaurantResponse,
    RestaurantUpdate,
)
from app.utils.location import calculate_distance_km

router = APIRouter(prefix="/restaurants", tags=["Restaurants"])

TRAVEL_SPEED_KM_PER_HOUR = 20


def restaurant_is_open_now(restaurant: Restaurant) -> bool:
    """Check availability using the restaurant's configured opening hours."""
    if not restaurant.is_active or not restaurant.is_open:
        return False

    if restaurant.is_temporarily_closed:
        return False

    if restaurant.opening_time is None or restaurant.closing_time is None:
        # Preserve compatibility with restaurants without configured hours.
        return True

    current_time = datetime.now().time().replace(tzinfo=None)
    opening = restaurant.opening_time
    closing = restaurant.closing_time

    if opening <= closing:
        return opening <= current_time <= closing

    # Supports restaurants that close after midnight.
    return current_time >= opening or current_time <= closing


@router.post(
    "",
    response_model=RestaurantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_restaurant(
    restaurant_data: RestaurantCreate,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    existing_restaurant = await db.scalar(
        select(Restaurant).where(Restaurant.owner_id == current_user.id)
    )

    if existing_restaurant:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already own a restaurant",
        )

    restaurant = Restaurant(
        owner_id=current_user.id,
        name=restaurant_data.name,
        description=restaurant_data.description,
        phone=restaurant_data.phone,
        email=(
            str(restaurant_data.email).lower()
            if restaurant_data.email
            else None
        ),
        address=restaurant_data.address,
        city=restaurant_data.city,
        cuisine_type=restaurant_data.cuisine_type,
        latitude=restaurant_data.latitude,
        longitude=restaurant_data.longitude,
        opening_time=restaurant_data.opening_time,
        closing_time=restaurant_data.closing_time,
        delivery_radius_km=restaurant_data.delivery_radius_km,
        min_order_value=restaurant_data.min_order_value,
        avg_preparation_minutes=restaurant_data.avg_preparation_minutes,
        is_temporarily_closed=restaurant_data.is_temporarily_closed,
        is_active=True,
        is_open=True,
    )

    db.add(restaurant)
    await db.commit()
    await db.refresh(restaurant)

    return restaurant


@router.get("", response_model=list[RestaurantResponse])
async def list_restaurants(
    city: str | None = Query(default=None, min_length=2),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Restaurant).where(Restaurant.is_active.is_(True))

    if current_user.role == "Customer":
        query = query.where(Restaurant.is_open.is_(True))

    if city:
        query = query.where(Restaurant.city.ilike(f"%{city.strip()}%"))

    query = query.order_by(Restaurant.name).offset(skip).limit(limit)
    result = await db.scalars(query)
    return list(result.all())


@router.get("/nearby", response_model=list[NearbyRestaurantResponse])
async def nearby_restaurants(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    name: str | None = Query(default=None, min_length=1),
    cuisine_type: str | None = Query(default=None, min_length=1),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Restaurant).where(
        Restaurant.is_active.is_(True),
    )

    if current_user.role == "Customer":
        query = query.where(Restaurant.is_open.is_(True))

    if name:
        query = query.where(Restaurant.name.ilike(f"%{name.strip()}%"))

    if cuisine_type:
        query = query.where(
            Restaurant.cuisine_type.ilike(f"%{cuisine_type.strip()}%")
        )

    if min_rating is not None:
        query = query.where(Restaurant.average_rating >= min_rating)

    result = await db.scalars(query.order_by(Restaurant.name))
    restaurants = list(result.all())

    nearby = []

    for restaurant in restaurants:
        if not restaurant_is_open_now(restaurant):
            continue

        if restaurant.latitude is None or restaurant.longitude is None:
            continue

        distance_km = calculate_distance_km(
            latitude,
            longitude,
            float(restaurant.latitude),
            float(restaurant.longitude),
        )

        if distance_km > float(restaurant.delivery_radius_km):
            continue

        travel_minutes = ceil(
            (distance_km / TRAVEL_SPEED_KM_PER_HOUR) * 60
        )
        estimated_delivery_minutes = (
            restaurant.avg_preparation_minutes + travel_minutes
        )

        nearby.append(
            {
                "restaurant": restaurant,
                "distance_km": distance_km,
                "estimated_delivery_minutes": estimated_delivery_minutes,
            }
        )

    nearby.sort(key=lambda item: item["distance_km"])

    # Apply pagination after distance sorting so the nearest results come first.
    page = nearby[skip : skip + limit]

    return [
        NearbyRestaurantResponse(
            **{
                column.name: getattr(item["restaurant"], column.name)
                for column in Restaurant.__table__.columns
            },
            distance_km=item["distance_km"],
            estimated_delivery_minutes=item["estimated_delivery_minutes"],
        )
        for item in page
    ]


@router.get("/{restaurant_id}", response_model=RestaurantResponse)
async def get_restaurant(
    restaurant_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    restaurant = await db.get(Restaurant, restaurant_id)

    if (
        restaurant is None
        or not restaurant.is_active
        or (
            current_user.role == "Customer"
            and not restaurant.is_open
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    return restaurant


@router.patch(
    "/{restaurant_id}",
    response_model=RestaurantResponse,
)
async def update_restaurant(
    restaurant_id: int,
    restaurant_data: RestaurantUpdate,
    current_user: User = Depends(require_roles("RestaurantOwner")),
    db: AsyncSession = Depends(get_db),
):
    restaurant = await db.get(Restaurant, restaurant_id)

    if restaurant is None or restaurant.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    update_data = restaurant_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(restaurant, field, value)

    await db.commit()
    await db.refresh(restaurant)

    return restaurant


@router.delete("/{restaurant_id}", status_code=status.HTTP_200_OK)
async def deactivate_restaurant(
    restaurant_id: int,
    current_user: User = Depends(require_roles("RestaurantOwner", "Admin")),
    db: AsyncSession = Depends(get_db),
):
    restaurant = await db.get(Restaurant, restaurant_id)

    if restaurant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    if (
        current_user.role != "Admin"
        and restaurant.owner_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    restaurant.is_active = False
    await db.commit()

    return {"message": "Restaurant deactivated successfully"}
