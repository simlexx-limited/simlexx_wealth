"""
Create a Simlexx administrator, or reset an administrator's password.

Run it inside the api container (nothing runs at startup, and there is no
public endpoint for this):

    docker compose exec api python -m app.scripts.create_admin
    docker compose exec api python -m app.scripts.create_admin --reset-password

Running it again with a different phone number creates another admin, so
losing one account never locks everybody out.
"""
import argparse
import asyncio
import getpass
import sys
from typing import Optional

import phonenumbers
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal, engine
from app.model.mod_enum import AccountStatus, AuthProvider, UserRole
from app.model.models import Users
from app.schema.phone_auth_schema import match_pattern
from app.services.auth.phone.token.hash_password import generate_hash
from app.services.logs.system_log_service import SystemLogService

ENDPOINT = "cli:create_admin"


class AdminSetupError(Exception):
    """Raised when the requested admin can't be created or updated."""


def normalize_phone_number(value: str) -> str:
    try:
        parsed = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        raise AdminSetupError("Phone number must be in international format, e.g. +256700000000")
    if not phonenumbers.is_valid_number(parsed):
        raise AdminSetupError("That is not a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def validate_email(value: str) -> str:
    try:
        return str(TypeAdapter(EmailStr).validate_python(value))
    except ValidationError:
        raise AdminSetupError("That is not a valid email address")


def validate_password(value: str) -> str:
    if len(value) < 12:
        raise AdminSetupError("An admin password must be at least 12 characters long")
    if not match_pattern().match(value):
        raise AdminSetupError(
            "Password must include an uppercase letter, a lowercase letter, "
            "a number, and a special character"
        )
    return value


async def create_admin(
    session: AsyncSession,
    phone_number: str,
    email_address: str,
    password: str,
    reset_password: bool = False,
) -> tuple[Users, bool]:
    """
    Create an administrator, or with reset_password=True set a new password on
    an existing one. Returns (user, created).

    It never turns an existing non-admin account into an admin, and never
    changes the details of an admin that already exists unless asked to reset.
    """
    phone_number = normalize_phone_number(phone_number)
    email_address = validate_email(email_address)
    validate_password(password)

    found = await session.execute(
        select(Users).where(
            or_(Users.phone_number == phone_number, Users.email_address == email_address)
        )
    )
    existing = found.scalars().first()
    new_hash = await asyncio.to_thread(generate_hash, password=password, rounds=12)

    if existing is not None:
        if existing.role != UserRole.ADMIN:
            raise AdminSetupError(
                "That phone number or email already belongs to a non-admin account"
            )
        if not reset_password:
            raise AdminSetupError(
                "That admin already exists. Use --reset-password to set a new password"
            )
        existing.password = new_hash
        existing.token_version = (existing.token_version or 0) + 1  # sign out its sessions
        await session.commit()
        await SystemLogService(session).create_log(
            action="reset_admin_password",
            entity="Users",
            user_id=str(existing.id),
            entity_id=str(existing.id),
            description="Admin password reset from the command line",
            endpoint=ENDPOINT,
        )
        return existing, False

    if reset_password:
        raise AdminSetupError("No admin with that phone number or email to reset")

    admin = Users(
        phone_number=phone_number,
        email_address=email_address,
        password=new_hash,
        auth_provider=AuthProvider.PHONE_NUMBER,
        role=UserRole.ADMIN,
        status=AccountStatus.ACTIVE,
        phone_number_verified=True,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    await SystemLogService(session).create_log(
        action="create_admin",
        entity="Users",
        user_id=str(admin.id),
        entity_id=str(admin.id),
        description="Admin account created from the command line",
        endpoint=ENDPOINT,
    )
    return admin, True


def read_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        raise AdminSetupError("Passwords did not match")
    return password


async def run(args: argparse.Namespace) -> None:
    phone_number = args.phone or input("Phone number (e.g. +256700000000): ").strip()
    email_address = args.email or input("Email address: ").strip()
    password = read_password()

    async with AsyncSessionLocal() as session:
        admin, created = await create_admin(
            session,
            phone_number=phone_number,
            email_address=email_address,
            password=password,
            reset_password=args.reset_password,
        )
    print(f"Admin {'created' if created else 'password reset'}: {admin.phone_number}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create or reset a Simlexx administrator")
    parser.add_argument("--phone", help="Admin phone number (asked for if left out)")
    parser.add_argument("--email", help="Admin email address (asked for if left out)")
    parser.add_argument(
        "--reset-password", action="store_true",
        help="Set a new password on an existing admin instead of creating one",
    )
    args = parser.parse_args(argv)

    async def runner() -> None:
        try:
            await run(args)
        finally:
            await engine.dispose()

    try:
        asyncio.run(runner())
    except AdminSetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
