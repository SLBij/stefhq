"""Run once to create your user account: uv run python seed.py"""
import asyncio
import getpass

import sqlalchemy as sa

from api.auth import hash_password
from database import async_session_factory, init_db
from models.db import User


async def main():
    await init_db()
    email = input("Email: ")
    password = getpass.getpass("Password: ")

    async with async_session_factory() as session:
        existing = await session.execute(sa.select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            print("User already exists.")
            return
        user = User(email=email, hashed_password=hash_password(password))
        session.add(user)
        await session.commit()
        print(f"Created user: {email}")


if __name__ == "__main__":
    asyncio.run(main())
