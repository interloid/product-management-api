from unittest.mock import AsyncMock, patch

import pytest

from app.services.email_services import send_passcode_email


@pytest.mark.asyncio
async def test_send_passcode_email_renders_and_sends():
    with patch(
        "app.services.email_services.send_email", new_callable=AsyncMock
    ) as mock_send:
        await send_passcode_email(
            to_email="test@example.com",
            first_name="Alice",
            passcode="123456",
            expiry_minutes=5,
        )

        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["to_email"] == "test@example.com"
        assert call_kwargs["subject"] == "Your verification code"
        assert "123456" in call_kwargs["body"]
        assert "Alice" in call_kwargs["body"]
        assert call_kwargs["html"] is True
