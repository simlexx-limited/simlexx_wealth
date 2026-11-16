from unittest.mock import patch

import httpx
import pytest

from app.config.setting import get_settings
from app.services.sms.africastalking_client import send_via_africastalking
from app.services.sms.sms_errors import (
    InvalidSmsRecipientError,
    SmsRejectedError,
    SmsServiceUnavailableError,
)

TO = "+256700000001"


@pytest.fixture(autouse=True)
def at_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "AT_USERNAME", "sandbox")
    monkeypatch.setattr(settings, "AT_API_KEY", "test-key")
    monkeypatch.setattr(settings, "AT_ENVIRONMENT", "sandbox")
    monkeypatch.setattr(settings, "AT_SENDER_ID", "")
    return settings


def at_body(status_code=101, status="Success", message_id="ATXid_1", cost="UGX 25.0000"):
    return {"SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [
        {"statusCode": status_code, "number": TO, "status": status, "cost": cost, "messageId": message_id},
    ]}}


def fake_post(status=201, body=None, error=None, raw=None, calls=None):
    async def post(self, url, data=None, headers=None):
        if calls is not None:
            calls.append({"url": url, "data": data, "headers": headers})
        if error:
            raise error
        if raw is not None:
            return httpx.Response(status, text=raw)
        return httpx.Response(status, json=body)
    return patch.object(httpx.AsyncClient, "post", post)


class TestRequest:
    @pytest.mark.asyncio
    async def test_sends_the_form_and_api_key_to_the_sandbox(self):
        calls: list = []
        with fake_post(body=at_body(), calls=calls):
            await send_via_africastalking(TO, "hello")

        call = calls[0]
        assert call["url"] == "https://api.sandbox.africastalking.com/version1/messaging"
        assert call["data"] == {"username": "sandbox", "to": TO, "message": "hello"}
        assert call["headers"] == {"apiKey": "test-key", "Accept": "application/json"}

    @pytest.mark.asyncio
    async def test_live_environment_and_sender_id(self, at_settings, monkeypatch):
        monkeypatch.setattr(at_settings, "AT_ENVIRONMENT", "live")
        monkeypatch.setattr(at_settings, "AT_SENDER_ID", "SIMLEXX")
        calls: list = []
        with fake_post(body=at_body(), calls=calls):
            await send_via_africastalking(TO, "hello")

        assert calls[0]["url"] == "https://api.africastalking.com/version1/messaging"
        assert calls[0]["data"]["from"] == "SIMLEXX"


class TestResponse:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [100, 101, 102])
    async def test_accepted_returns_the_message_id_and_cost(self, code):
        with fake_post(body=at_body(status_code=code)):
            result = await send_via_africastalking(TO, "hello")

        assert result.provider == "africastalking"
        assert result.message_id == "ATXid_1"
        assert result.cost == "UGX 25.0000"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code, error", [
        (403, InvalidSmsRecipientError),
        (404, InvalidSmsRecipientError),
        (406, InvalidSmsRecipientError),
        (401, SmsRejectedError),
        (402, SmsRejectedError),
        (405, SmsRejectedError),
        (407, SmsRejectedError),
        (999, SmsRejectedError),
        (500, SmsServiceUnavailableError),
        (501, SmsServiceUnavailableError),
        (502, SmsServiceUnavailableError),
    ])
    async def test_recipient_status_codes_map_to_errors(self, code, error):
        with fake_post(body=at_body(status_code=code, status="Failed")):
            with pytest.raises(error):
                await send_via_africastalking(TO, "hello")

    @pytest.mark.asyncio
    async def test_timeout_is_retryable(self):
        with fake_post(error=httpx.ConnectTimeout("slow")):
            with pytest.raises(SmsServiceUnavailableError):
                await send_via_africastalking(TO, "hello")

    @pytest.mark.asyncio
    async def test_http_5xx_is_retryable(self):
        with fake_post(status=503, raw="down"):
            with pytest.raises(SmsServiceUnavailableError):
                await send_via_africastalking(TO, "hello")

    @pytest.mark.asyncio
    async def test_http_401_bad_api_key_is_rejected(self):
        with fake_post(status=401, raw="The supplied authentication is invalid"):
            with pytest.raises(SmsRejectedError):
                await send_via_africastalking(TO, "hello")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["not json", '{"SMSMessageData": {"Recipients": []}}', "{}"])
    async def test_malformed_response_is_retryable(self, raw):
        with fake_post(raw=raw):
            with pytest.raises(SmsServiceUnavailableError):
                await send_via_africastalking(TO, "hello")
