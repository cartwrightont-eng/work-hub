"""One function sends mail. MAIL_BACKEND picks how: 'console' (default, logs only), 'smtp', or 'memory' (tests)."""
import logging, os, re, smtplib
from email.message import EmailMessage

OUTBOX = []                                     # used by the 'memory' backend
_ADDR = re.compile(r"^[^\s@,;<>]+@[^\s@,;<>]+$")

def clean_subject(s):
    """Subjects come from user-typed titles: collapse every line break so nobody can inject headers."""
    return " ".join(str(s).replace("\r", " ").replace("\n", " ").split())[:200]

def send(to, subject, body):
    if not _ADDR.match(to or ""): raise ValueError("invalid recipient address")
    subject = clean_subject(subject)
    backend = os.environ.get("MAIL_BACKEND", "console")
    if backend == "memory":
        OUTBOX.append((to, subject, body))
    elif backend == "console":
        logging.getLogger("mail").info("would send to=%s subject=%s", to, subject)
    elif backend == "smtp":
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
        msg.set_content(body)
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
            if os.environ.get("SMTP_TLS", "1") != "0": s.starttls()
            if os.environ.get("SMTP_USER"): s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
    else:
        raise RuntimeError(f"unknown MAIL_BACKEND {backend!r}")
