from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from app.core.settings import settings
from app.core.smtp import send_email


@pytest.mark.asyncio
async def test_send_email_sends_plain_text_email():
    with patch(
        "app.core.smtp.aiosmtplib.send",
        new_callable=AsyncMock,
    ) as mock_send:
        await send_email(
            to_email="user@example.com",
            subject="Test Subject",
            body="Test body",
        )

    mock_send.assert_awaited_once()

    message = mock_send.call_args.args[0]

    assert message["To"] == "user@example.com"
    assert message["Subject"] == "Test Subject"
    assert message.get_content().strip() == "Test body"


@pytest.mark.asyncio
async def test_send_email_sends_html_email():
    html_body = "<h1>Hello</h1>"

    with patch(
        "app.core.smtp.aiosmtplib.send",
        new_callable=AsyncMock,
    ) as mock_send:
        await send_email(
            to_email="user@example.com",
            subject="HTML Subject",
            body=html_body,
            html=True,
        )

    mock_send.assert_awaited_once()

    message = mock_send.call_args.args[0]

    assert message["To"] == "user@example.com"
    assert message["Subject"] == "HTML Subject"

    assert message.is_multipart()

    html_part = message.get_payload()[0]

    assert html_part.get_content_type() == "text/html"
    assert html_part.get_content().strip() == html_body


@pytest.mark.asyncio
async def test_send_email_uses_smtp_settings():
    test_password = SecretStr("test-smtp-password")

    with (
        patch.object(settings, "SMTP_PASSWORD", test_password),
        patch(
            "app.core.smtp.aiosmtplib.send",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await send_email(
            to_email="user@example.com",
            subject="Test",
            body="Hello",
        )

    mock_send.assert_awaited_once()

    kwargs = mock_send.call_args.kwargs

    assert kwargs["hostname"] == settings.SMTP_HOST
    assert kwargs["port"] == settings.SMTP_PORT
    assert kwargs["username"] == settings.SMTP_USERNAME
    assert kwargs["password"] == test_password.get_secret_value()
    assert kwargs["start_tls"] == settings.SMTP_START_TLS


@pytest.mark.asyncio
async def test_send_email_propagates_smtp_error():
    with patch(
        "app.core.smtp.aiosmtplib.send",
        new_callable=AsyncMock,
        side_effect=RuntimeError("SMTP connection failed"),
    ):
        with pytest.raises(
            RuntimeError,
            match="SMTP connection failed",
        ):
            await send_email(
                to_email="user@example.com",
                subject="Test",
                body="Hello",
            )
