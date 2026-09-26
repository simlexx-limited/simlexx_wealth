from typing import Annotated

import phonenumbers
from pydantic import AfterValidator


def normalize_phone_number(value: str) -> str:
    try:
        parsed = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        raise ValueError("phone_number must be in international format, e.g. +256700000000")
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("phone_number is not a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


# A phone number in international format, stored the same way everywhere (E.164).
PhoneNumber = Annotated[str, AfterValidator(normalize_phone_number)]
