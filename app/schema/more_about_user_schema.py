from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from app.model.mod_enum import UserAccountStatus


class MoreAboutUserCreate(BaseModel):
    """
    Payload required to create a new MoreAboutUsers profile.
    The user_id is taken from the authenticated JWT — not accepted in the body.
    """
    first_name: str = Field(description="User first name")
    last_name: str = Field(description="User last name")
    middle_name: Optional[str] = Field(default=None, description="User middle name (optional)")
    date_of_birth: datetime = Field(description="Date of birth (ISO 8601)")
    nationality: str = Field(description="Nationality")
    country_of_residence: str = Field(description="Country of residence")
    city_of_residence: str = Field(description="City of residence")
    full_address: str = Field(description="Full residential address")
    profession: str = Field(description="Profession or occupation")
    document_type: str = Field(description="Identity document type (e.g. passport, national_id)")
    document_id: str = Field(description="Document identification number")
    document_link: str = Field(description="URL/path to the uploaded document")
    document_expiry_date: date = Field(description="Document expiry date")
    profile_picture: str = Field(description="URL/path to the profile picture")


class MoreAboutUserUpdate(BaseModel):
    """
    Partial-update payload — every field is optional.
    Only provided fields will be written to the database.
    """
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_name: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    nationality: Optional[str] = None
    country_of_residence: Optional[str] = None
    city_of_residence: Optional[str] = None
    full_address: Optional[str] = None
    profession: Optional[str] = None
    document_type: Optional[str] = None
    document_id: Optional[str] = None
    document_link: Optional[str] = None
    document_expiry_date: Optional[date] = None
    profile_picture: Optional[str] = None
    profile_verification_complete: Optional[UserAccountStatus] = None
    reviewer_id: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class MoreAboutUserResponse(BaseModel):
    """
    Response shape for a MoreAboutUsers record.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    first_name: str
    last_name: str
    middle_name: Optional[str] = None
    date_of_birth: datetime
    nationality: str
    country_of_residence: str
    city_of_residence: str
    full_address: str
    profession: str
    document_type: str
    document_id: str
    document_link: str
    document_expiry_date: date
    profile_picture: str
    profile_verification_complete: UserAccountStatus
    reviewer_id: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    updated_at: datetime
