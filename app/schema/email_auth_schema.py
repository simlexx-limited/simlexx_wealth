#!/usr/bin/env python3

import phonenumbers
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, field_validator, ConfigDict

from app.model.mod_enum import UserRole, AuthProvider, AccountStatus, UserAccountStatus

class EmailSignINSchema(BaseModel):
    """
    Details from firebase
    """
    firebase_uid:str = Field(description="Firebase UID field")
    email:EmailStr = Field(description="User email address")
    email_verified:bool = Field(description="Email verified status")
    auth_provider:AuthProvider = Field(description="Auth provider")

class User_signup_schema(BaseModel):
    """
    User login schema
    """
    phone_number:str = Field(description="User phone number")
    role:UserRole = Field(description="User role")

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

class User_signup_response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:str
    phone_number:str
    email_address:EmailStr|None = None
    role:UserRole
    status:AccountStatus
    profile_verification_complete:UserAccountStatus
    created_at:datetime
