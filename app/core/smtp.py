from email.message import EmailMessage

import aiosmtplib

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)


async def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    html: bool = False,
) -> None:
    message = EmailMessage()

    message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    message["To"] = to_email
    message["Subject"] = subject

    if html:
        message.add_alternative(
            body,
            subtype="html",
        )
    else:
        message.set_content(body)

    logger.info(
        "Sending email | to=%s | from=%s | host=%s | port=%s",
        to_email,
        settings.SMTP_FROM_EMAIL,
        settings.SMTP_HOST,
        settings.SMTP_PORT,
    )
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USERNAME,
            password=(
                settings.SMTP_PASSWORD.get_secret_value()
                if settings.SMTP_PASSWORD is not None
                else None
            ),
            start_tls=settings.SMTP_START_TLS,
        )
        logger.info(
            "Email successfully accepted by SMTP server | to=%s",
            to_email,
        )

    except Exception:
        logger.exception(
            "Failed to send email | to=%s",
            to_email,
        )
        raise
