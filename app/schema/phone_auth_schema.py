import re
import phonenumbers
from typing import Optional
from pydantic import BaseModel, field_validator, Field, EmailStr
from app.model.mod_enum import UserRole

def match_pattern():
    return re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).*$")

class PhoneNumberSignup(BaseModel):
    """
    BaseModel for phone signup
    """
    phone_number:str = Field(description="User phone number")
    password:str = Field(description="User password")
    confirm_password:str = Field(description="User confirm password")
    email_address:Optional[EmailStr] = Field(description="User email address")
    role:UserRole = Field(description="User provided role")

    @field_validator("password")
    @classmethod
    def validate_user_password(cls, value:str):
        if len(value) < 8:
            raise ValueError("Password length must be at least 8 chars")
        if not match_pattern().match(value):
            raise ValueError("Password must include an uppercase letter, a lowercase letter" \
            "a  number, and a special character")
        return value

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        try:
            parsed = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException:
            raise ValueError("phone_number must be in international format, e.g. +250250123456")
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("phone_number is not a valid phone number")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

class PhoneLogin(BaseModel):
    """
    Base Model for Phone login
    """
    phone_number:str = Field(description="User phone number")
    password:str = Field(description="User password")

    @field_validator("password")
    @classmethod
    def validate_user_password(cls, value:str):
        if len(value) < 8:
            raise ValueError("Password length must be at least 8 chars")
        if not match_pattern().match(value):
            raise ValueError("Password must include an uppercase letter, a lowercase letter" \
                             "a  number, and a special character")
        return value

class ForgotPasswordEmail(BaseModel):
    """
    Schema for requesting OTP Code for changing password via Email
    """
    email_address:EmailStr = Field(description="User Email Address")

class ForgotPasswordPhone(BaseModel):
    """
    Schema for requesting OTP for changing password via Phone
    """
    phone_number:str = Field(description="User Phone number")
