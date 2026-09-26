from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, computed_field, AnyHttpUrl, AnyUrl

from app.model.mod_enum import UserAccountStatus, DocumentType
from app.schema.country import CountryCode, country_name


class MoreAboutUserCreate(BaseModel):
    """
    Payload required to create a new MoreAboutUsers profile.
    The user_id is taken from the authenticated JWT — not accepted in the body.
    """
    first_name: str = Field(description="User first name")
    last_name: str = Field(description="User last name")
    middle_name: Optional[str] = Field(default=None, description="User middle name (optional)")
    date_of_birth: date = Field(description="Date of birth (ISO 8601)")
    nationality: CountryCode = Field(description="Country of citizenship (ISO code, e.g. UG)")
    country_of_residence: CountryCode = Field(description="Country of residence (ISO code, e.g. UG)")
    city_of_residence: str = Field(description="City of residence")
    full_address: str = Field(description="Full residential address")
    profession: str = Field(description="Profession or occupation")
    document_type: DocumentType = Field(description="Identity document type")
    document_id: str = Field(description="Document identification number")
    document_expiry_date: date = Field(description="Document expiry date")


class MoreAboutUserUpdate(BaseModel):
    """
    Partial update of the personal details a client can edit on their own
    profile. Every field is optional; only provided fields are written.

    Deliberately excludes the uploaded file links and everything about the
    review (status, reviewer, comment, timestamps): a client must not be able
    to set those. The files are replaced by uploading new ones, and the review
    is decided by a reviewer (see review_schema).
    """
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_name: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    nationality: Optional[CountryCode] = None
    country_of_residence: Optional[CountryCode] = None
    city_of_residence: Optional[str] = None
    full_address: Optional[str] = None
    profession: Optional[str] = None
    document_type: Optional[DocumentType] = None
    document_id: Optional[str] = None
    document_expiry_date: Optional[date] = None


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
    document_type: DocumentType
    document_id: str
    document_link: str
    document_expiry_date: date
    profile_picture: str
    profile_verification_complete: UserAccountStatus
    reviewer_id: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def nationality_name(self) -> str:
        return country_name(self.nationality)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def country_of_residence_name(self) -> str:
        return country_name(self.country_of_residence)

class UploadedURL(BaseModel):
    link:AnyUrl|AnyHttpUrl
