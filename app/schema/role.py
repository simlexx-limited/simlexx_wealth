from typing import Annotated

from pydantic import AfterValidator

from app.config.setting import get_settings
from app.model.mod_enum import UserRole


def validate_self_registration_role(value: UserRole) -> UserRole:
    if value.value not in get_settings().SELF_REGISTRATION_ROLES:
        raise ValueError("This account type can't be created by self-registration")
    return value


# A role a person is allowed to pick for themselves when signing up.
SelfRegistrationRole = Annotated[UserRole, AfterValidator(validate_self_registration_role)]
