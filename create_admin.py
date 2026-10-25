
import asyncio
import getpass

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.entities import User
from app.auth.security import hash_password


async def create_admin():
    print("=== Food Delivery System: Create Admin ===")

    name = input("Admin name: ").strip()
    email = input("Admin email: ").strip().lower()
    password = getpass.getpass("Admin password (hidden): ")
    confirm_password = getpass.getpass("Confirm password: ")

    if not name or not email or not password:
        print("Error: Name, email, and password are required.")
        return

    if password != confirm_password:
        print("Error: Passwords do not match.")
        return

    if len(password) < 8:
        print("Error: Password must contain at least 8 characters.")
        return

    async with AsyncSessionLocal() as db:
        existing_user = await db.scalar(
            select(User).where(User.email == email)
        )

        if existing_user:
            print("Error: A user with this email already exists.")
            print("No existing account was changed.")
            return

        admin = User(
            full_name=name,
            email=email,
            hashed_password=hash_password(password),
            role="Admin",
            is_active=True,
            is_verified=True,
        )

        db.add(admin)

        try:
            await db.commit()
            print("\nAdmin account created successfully.")
            print(f"Email: {email}")
            print("You can now log in through Swagger.")
        except Exception:
            await db.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(create_admin())
