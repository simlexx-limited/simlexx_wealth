from pydantic import BaseModel, Field, field_validator

from app.model.mod_enum import UserAccountStatus
from app.schema.more_about_user_schema import MoreAboutUserSummary


class ReviewDecision(BaseModel):
    """
    A reviewer's decision on a client's profile. The reviewer and the time are
    recorded by the server, never taken from here.
    """
    decision: UserAccountStatus = Field(description="approved or rejected")
    comment: str = Field(
        min_length=3, max_length=1000,
        description="Why: required for approvals and rejections. A rejected client sees this "
                    "and can fix their details and ask for another review",
    )

    @field_validator("decision")
    @classmethod
    def decision_must_be_final(cls, value: UserAccountStatus) -> UserAccountStatus:
        if value == UserAccountStatus.PENDING:
            raise ValueError("A review must approve or reject the profile")
        return value

    @field_validator("comment")
    @classmethod
    def comment_must_say_something(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Comment must be at least 3 characters")
        return value


class ReviewPage(BaseModel):
    items: list[MoreAboutUserSummary]
    total: int
    page: int
    limit: int
