from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.model.mod_enum import AccountStatus
from app.schema.phone import PhoneNumber


class AgentCreate(BaseModel):
    """
    A person to create an agent account for. The account starts with a temporary
    password that is emailed to them and must be changed at first sign-in.
    """
    first_name: str = Field(min_length=1, description="First name")
    last_name: str = Field(min_length=1, description="Last name")
    email_address: EmailStr = Field(description="The temporary password is sent here")
    phone_number: PhoneNumber = Field(description="Phone number, international format")
    job_title: Optional[str] = Field(default=None, description="Job title")


class AgentStatusChange(BaseModel):
    active: bool = Field(description="true to reactivate the account, false to deactivate it")
    reason: Optional[str] = Field(default=None, description="Why; recorded in the audit log and sent to the person")


class AgentResponse(BaseModel):
    user_id: str
    first_name: str
    last_name: str
    email_address: Optional[str] = None
    phone_number: str
    job_title: Optional[str] = None
    status: AccountStatus
    must_change_password: bool
    created_at: datetime


class AgentCreated(BaseModel):
    status: bool = True
    message: str
    credentials_email_queued: bool = Field(
        description="false if the email could not be queued; use reset-password to send a new link"
    )
    temporary_password: str = Field(
        description="Temporary, while email delivery is unreliable - relay this to the agent by hand"
    )
    data: AgentResponse
