"""
Sends one application email when the user clicks Send. Never called by the scheduler.

Configure in .env (Gmail: create an App Password at https://myaccount.google.com/apppasswords):
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=465
    SMTP_USER=you@gmail.com
    SMTP_PASSWORD=<16-character app password>
    SMTP_FROM_NAME=Your Name        (optional)
"""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
import mimetypes
import os
from pathlib import Path
import re
import smtplib
from typing import Any, Dict, Optional

from src.core.db import outreach_for, outreach_sent_since, record_outreach

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def smtp_config() -> Dict[str, Any]:
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "465")),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").replace(" ", ""),
        "from_name": os.getenv("SMTP_FROM_NAME", "").strip() or _profile_name(),
    }


def _profile_name() -> str:
    from src.web.agent import load_profile_data
    return (load_profile_data().get("name") or "").strip()


def is_configured() -> bool:
    cfg = smtp_config()
    return bool(cfg["user"] and cfg["password"])


def resume_file() -> Optional[Path]:
    """The original resume the user uploaded (PDF preferred), if any."""
    for name in ("resume.pdf", "resume_upload.pdf", "resume_upload.md", "resume_upload.txt"):
        path = DATA_DIR / name
        if path.exists():
            return path
    return None


def _connect(cfg: Dict[str, Any]) -> smtplib.SMTP:
    if cfg["port"] == 465:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
        server.starttls()
    server.login(cfg["user"], cfg["password"])
    return server


def send_email(item_ref: str, to: str, subject: str, body: str, attach_resume: bool, daily_cap: int,
               allow_repeat: bool = False) -> Dict[str, Any]:
    """Validate, enforce limits, send, and log. Returns {"ok": bool, "error"?: str}."""
    cfg = smtp_config()
    if not is_configured():
        return {"ok": False, "error": "Email isn't set up. Add SMTP_USER and SMTP_PASSWORD to .env."}
    to = (to or "").strip()
    if not EMAIL_RE.match(to):
        return {"ok": False, "error": f"'{to}' doesn't look like an email address."}
    if not subject.strip() or len(body.split()) < 15:
        return {"ok": False, "error": "Subject or message is too short to send."}

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    if outreach_sent_since(since) >= daily_cap:
        return {"ok": False, "error": f"Daily limit reached ({daily_cap} emails in 24h). This protects your Gmail account."}
    if not allow_repeat and any(o["ok"] and o["to_email"].lower() == to.lower() for o in outreach_for(item_ref)):
        return {"ok": False, "error": f"You already emailed {to} about this post."}

    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["user"])) if cfg["from_name"] else cfg["user"]
    msg["To"] = to
    msg["Subject"] = subject.strip()
    msg["Message-ID"] = make_msgid()
    msg.set_content(body.strip())

    attachment = resume_file() if attach_resume else None
    if attachment:
        ctype = mimetypes.guess_type(attachment.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        filename = f"{(cfg['from_name'] or 'Resume').replace(' ', '_')}_Resume{attachment.suffix}"
        msg.add_attachment(attachment.read_bytes(), maintype=maintype, subtype=subtype, filename=filename)

    try:
        with _connect(cfg) as server:
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        error = "Gmail rejected the login. Use an App Password, not your normal password."
        record_outreach(item_ref, to, subject, body, attachment and attachment.name, False, error)
        return {"ok": False, "error": error}
    except Exception as e:
        record_outreach(item_ref, to, subject, body, attachment and attachment.name, False, str(e)[:300])
        return {"ok": False, "error": f"Couldn't send: {e}"}

    record_outreach(item_ref, to, subject, body, attachment and attachment.name, True)
    return {"ok": True, "attached": attachment.name if attachment else None}


def send_test_email() -> Dict[str, Any]:
    """Send a short test message to the user's own address."""
    cfg = smtp_config()
    if not is_configured():
        return {"ok": False, "error": "Email isn't set up. Add SMTP_USER and SMTP_PASSWORD to .env."}
    msg = EmailMessage()
    msg["From"] = cfg["user"]
    msg["To"] = cfg["user"]
    msg["Subject"] = "SARTHI email test"
    msg.set_content("If you can read this, SARTHI can send application emails from this account.")
    try:
        with _connect(cfg) as server:
            server.send_message(msg)
        return {"ok": True}
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "error": "Gmail rejected the login. Use an App Password, not your normal password."}
    except Exception as e:
        return {"ok": False, "error": f"Couldn't send: {e}"}
