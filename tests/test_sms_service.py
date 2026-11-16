from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.config.setting import Settings, get_settings
from app.services.sms import sms_service, sms_templates
from app.services.sms.sms_errors import InvalidSmsRecipientError
from app.services.sms.sms_result import SmsResult


class TestProviderSwitch:
    @pytest.mark.asyncio
    async def test_console_provider_by_default(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "console")
        console = AsyncMock(return_value=SmsResult(provider="console"))
        at = AsyncMock()
        monkeypatch.setattr(sms_service, "send_via_console", console)
        monkeypatch.setattr(sms_service, "send_via_africastalking", at)

        await sms_service.send_sms("+256700000001", "hi")

        console.assert_awaited_once_with("+256700000001", "hi")
        at.assert_not_called()

    @pytest.mark.asyncio
    async def test_africastalking_provider(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "africastalking")
        at = AsyncMock(return_value=SmsResult(provider="africastalking", message_id="m1"))
        monkeypatch.setattr(sms_service, "send_via_africastalking", at)

        result = await sms_service.send_sms("+256700000001", "hi")

        assert result.message_id == "m1"

    @pytest.mark.asyncio
    async def test_failure_is_re_raised(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "africastalking")
        monkeypatch.setattr(sms_service, "send_via_africastalking", AsyncMock(side_effect=InvalidSmsRecipientError()))

        with pytest.raises(InvalidSmsRecipientError):
            await sms_service.send_sms("+256700000001", "hi")

    @pytest.mark.asyncio
    async def test_otp_sms_uses_the_template(self, monkeypatch):
        send = AsyncMock(return_value=SmsResult(provider="console"))
        monkeypatch.setattr(sms_service, "send_sms", send)

        await sms_service.send_otp_sms("+256700000001", "123456")

        send.assert_awaited_once_with("+256700000001", sms_templates.otp_message("123456"))


class TestMasking:
    def test_masks_the_middle_of_the_number(self):
        assert sms_service.mask_phone("+256712345123") == "+2567*****123"

    def test_short_input_is_fully_masked(self):
        assert sms_service.mask_phone("12345") == "*****"


class TestTemplates:
    @pytest.mark.parametrize("ttl", [300, 900, 3600, 86400, 90061])
    def test_otp_fits_one_sms_and_carries_the_code(self, monkeypatch, ttl):
        monkeypatch.setattr(get_settings(), "OTP_TTL_SECONDS", ttl)
        text = sms_templates.otp_message("12345678")
        assert "12345678" in text
        assert len(text) <= sms_templates.SMS_MAX_LENGTH

    def test_confirmations_fit_one_sms(self):
        big = Decimal("123456789012.12345678")
        assert len(sms_templates.deposit_confirmed(big, "UGX")) <= sms_templates.SMS_MAX_LENGTH
        assert len(sms_templates.withdrawal_confirmed(big, "UGX")) <= sms_templates.SMS_MAX_LENGTH


class TestSettings:
    def test_africastalking_needs_an_api_key(self):
        base = get_settings().model_dump()
        with pytest.raises(ValueError, match="AT_API_KEY"):
            Settings(**{**base, "SMS_PROVIDER": "africastalking", "AT_API_KEY": ""})

    def test_sandbox_and_live_urls(self):
        base = get_settings().model_dump()
        assert Settings(**{**base, "AT_ENVIRONMENT": "sandbox"}).at_sms_url.startswith("https://api.sandbox.")
        assert Settings(**{**base, "AT_ENVIRONMENT": "live"}).at_sms_url == "https://api.africastalking.com/version1/messaging"
