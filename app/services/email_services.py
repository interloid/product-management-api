from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.smtp import send_email

BASE_DIR = Path(__file__).resolve().parent.parent

template_env = Environment(
    loader=FileSystemLoader(BASE_DIR / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


async def send_passcode_email(
    *,
    to_email: str,
    first_name: str,
    passcode: str,
    expiry_minutes: int,
) -> None:
    template = template_env.get_template("emails/passcode.html")

    html_body = template.render(
        first_name=first_name,
        passcode=passcode,
        expiry_minutes=expiry_minutes,
    )

    await send_email(
        to_email=to_email,
        subject="Your verification code",
        body=html_body,
        html=True,
    )
