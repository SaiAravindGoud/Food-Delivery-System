
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entities import Address, User
from app.schemas.address import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
)


router = APIRouter(prefix="/addresses", tags=["Addresses"])


async def get_owned_address(
    address_id: int,
    user_id: int,
    db: AsyncSession,
) -> Address:
    result = await db.scalar(
        select(Address).where(
            Address.id == address_id,
            Address.user_id == user_id,
        )
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found",
        )

    return result


async def set_default_address(
    address: Address,
    user_id: int,
    db: AsyncSession,
) -> None:
    result = await db.scalars(
        select(Address).where(
            Address.user_id == user_id,
            Address.id != address.id,
        )
    )

    for other_address in result.all():
        other_address.is_default = False

    address.is_default = True


@router.post(
    "",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_address(
    address_data: AddressCreate,
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    existing_addresses = await db.scalars(
        select(Address).where(Address.user_id == current_user.id)
    )
    has_existing_addresses = bool(existing_addresses.first())

    data = address_data.model_dump()

    # Ensure the first address is the default address.
    if not has_existing_addresses:
        data["is_default"] = True

    address = Address(user_id=current_user.id, **data)
    db.add(address)
    await db.flush()

    if address.is_default:
        await set_default_address(address, current_user.id, db)

    await db.commit()
    await db.refresh(address)

    return address


@router.get("", response_model=list[AddressResponse])
async def list_addresses(
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(
        select(Address)
        .where(Address.user_id == current_user.id)
        .order_by(Address.is_default.desc(), Address.id.desc())
    )

    return list(result.all())


@router.put(
    "/{address_id}",
    response_model=AddressResponse,
)
async def update_address(
    address_id: int,
    address_data: AddressUpdate,
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    address = await get_owned_address(address_id, current_user.id, db)

    update_data = address_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(address, field, value)

    await db.commit()
    await db.refresh(address)

    return address


@router.delete("/{address_id}", status_code=status.HTTP_200_OK)
async def delete_address(
    address_id: int,
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    address = await get_owned_address(address_id, current_user.id, db)

    if address.is_default:
        remaining = await db.scalars(
            select(Address).where(
                Address.user_id == current_user.id,
                Address.id != address.id,
            )
        )
        remaining_addresses = list(remaining.all())

        if remaining_addresses:
            remaining_addresses[0].is_default = True

    await db.delete(address)
    await db.commit()

    return {"message": "Address deleted successfully"}


@router.put(
    "/{address_id}/default",
    response_model=AddressResponse,
)
async def make_default_address(
    address_id: int,
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    address = await get_owned_address(address_id, current_user.id, db)

    await set_default_address(address, current_user.id, db)
    await db.commit()
    await db.refresh(address)

    return address
