from typing import Annotated

import pycountry
from pydantic import AfterValidator


def validate_country_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 2 or pycountry.countries.get(alpha_2=code) is None:
        raise ValueError("Must be a valid ISO 3166-1 alpha-2 country code, e.g. UG")
    return code


CountryCode = Annotated[str, AfterValidator(validate_country_code)]


def country_name(code: str) -> str:
    country = pycountry.countries.get(alpha_2=code)
    return country.name if country else code
