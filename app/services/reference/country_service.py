import pycountry

from app.config.setting import get_settings
from app.services.auth.firebase_auth.auth_errors import UnsupportedCountryError


def ensure_country_served(code: str) -> None:
    if code not in get_settings().SERVED_COUNTRIES:
        raise UnsupportedCountryError()


def list_countries() -> list[dict]:
    served = get_settings().SERVED_COUNTRIES
    return sorted(
        (
            {"code": c.alpha_2, "name": c.name, "served": c.alpha_2 in served}
            for c in pycountry.countries
        ),
        key=lambda c: c["name"],
    )
