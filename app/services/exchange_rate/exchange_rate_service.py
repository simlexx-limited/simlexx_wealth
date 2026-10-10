#!/usr/bin/env python3
from decimal import Decimal
from typing import cast

import httpx

from app.config.setting import get_settings
from app.model.mod_enum import CurrencyType
from app.queue.redis import redis_client
from app.services.auth.firebase_auth.auth_errors import ExchangeRateUnavailableError

EXCHANGE_RATE_TTL_SECONDS = 24 * 60 * 60


async def get_exchange_rate(from_currency: CurrencyType, to_currency: CurrencyType) -> Decimal:
    """
    The rate to convert one unit of from_currency into to_currency, cached
    for a day since ExchangeRate-API's free tier only updates that often.

    Raises ExchangeRateUnavailableError if the rate can't be fetched.
    """
    if from_currency == to_currency:
        return Decimal("1")

    cache_key = f"fx_rate:{from_currency.value}:{to_currency.value}"
    cached = cast("str | None", await redis_client.get(cache_key))
    if cached is not None:
        return Decimal(cached)

    settings = get_settings()
    url = (
        f"https://v6.exchangerate-api.com/v6/{settings.EXCHANGE_RATE_API_KEY}"
        f"/pair/{from_currency.value}/{to_currency.value}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ExchangeRateUnavailableError() from exc

    if data.get("result") != "success":
        raise ExchangeRateUnavailableError()

    rate = Decimal(str(data["conversion_rate"]))
    await redis_client.set(cache_key, str(rate), ex=EXCHANGE_RATE_TTL_SECONDS)
    return rate
