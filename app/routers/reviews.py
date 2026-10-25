
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.entities import Order, Restaurant, Review, User

router = APIRouter(prefix="/reviews", tags=["Reviews"])


class ReviewCreate(BaseModel):
    order_id: int = Field(gt=0)
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    customer_id: int
    restaurant_id: int
    rating: int
    comment: str | None
    created_at: datetime


@router.post(
    "",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_review(
    payload: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "Customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only customers can submit reviews",
        )

    order = await db.scalar(
        select(Order).where(
            Order.id == payload.order_id,
            Order.customer_id == current_user.id,
        )
    )

    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    if order.status != "Delivered":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can review an order only after it is delivered",
        )

    existing_review = await db.scalar(
        select(Review).where(Review.order_id == order.id)
    )

    if existing_review is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This order has already been reviewed",
        )

    restaurant = await db.get(Restaurant, order.restaurant_id)

    if restaurant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    review = Review(
        order_id=order.id,
        customer_id=current_user.id,
        restaurant_id=order.restaurant_id,
        rating=payload.rating,
        comment=payload.comment.strip() if payload.comment else None,
    )

    db.add(review)

    try:
        await db.flush()

        average = await db.scalar(
            select(func.avg(Review.rating)).where(
                Review.restaurant_id == order.restaurant_id
            )
        )
        restaurant.average_rating = (
            Decimal(str(average)).quantize(Decimal("0.01"))
            if average is not None
            else Decimal("0.00")
        )

        await db.commit()
        await db.refresh(review)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This order has already been reviewed",
        ) from exc

    return review


@router.get(
    "/restaurant/{restaurant_id}",
    response_model=list[ReviewResponse],
)
async def list_restaurant_reviews(
    restaurant_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    restaurant = await db.get(Restaurant, restaurant_id)

    if restaurant is None or not restaurant.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    result = await db.scalars(
        select(Review)
        .where(Review.restaurant_id == restaurant_id)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .offset(skip)
        .limit(limit)
    )

    return list(result.all())
