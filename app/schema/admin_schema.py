import phonenumbers
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schema.phone import PhoneNumber
from app.schema.phone_auth_schema import match_pattern

# Administrators get a stricter password rule than clients
ADMIN_PASSWORD_MIN_LENGTH = 12


class AdminSignup(BaseModel):
    """
    First-run setup of the Simlexx administrator account.
    """
    phone_number:str = Field(description="Admin phone number, international format")
    email_address:EmailStr = Field(description="Admin email address")
    password:str = Field(description="Admin password")
    confirm_password:str = Field(description="Confirm the admin password")

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        try:
            parsed = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException:
            raise ValueError("phone_number must be in international format, e.g. +256700000000")
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("phone_number is not a valid phone number")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    @field_validator("password")
    @classmethod
    def validate_admin_password(cls, value: str) -> str:
        if len(value) < ADMIN_PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password length must be at least {ADMIN_PASSWORD_MIN_LENGTH} chars")
        if not match_pattern().match(value):
            raise ValueError("Password must include an uppercase letter, a lowercase letter, "
                             "a number, and a special character")
        return value


class AdminCreate(BaseModel):
    """
    Another Simlexx administrator, created by an existing one. They sign in with
    their phone number and a temporary password sent to their email.
    """
    first_name:str = Field(min_length=1, description="First name")
    last_name:str = Field(min_length=1, description="Last name")
    email_address:EmailStr = Field(description="The temporary password is sent here")
    phone_number:PhoneNumber = Field(description="Phone number, international format")
