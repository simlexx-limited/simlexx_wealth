from pydantic import BaseModel, Field, field_validator

from app.schema.phone_auth_schema import match_pattern


class PasswordChange(BaseModel):
    """
    Change the password of the signed-in account. Also how a temporary
    password (given to an account an administrator created) is replaced.
    """
    current_password:str = Field(description="The password you sign in with now")
    new_password:str = Field(description="The new password")
    confirm_password:str = Field(description="Confirm the new password")

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password length must be at least 8 chars")
        if not match_pattern().match(value):
            raise ValueError("Password must include an uppercase letter, a lowercase letter, "
                             "a number, and a special character")
        return value
