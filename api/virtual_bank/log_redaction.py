import logging
import re

REDACTED = "[REDACTED]"

_LONG_DIGIT_RUN_RE = re.compile(r"(?<![\w-])\d{10,19}(?![\w-])")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET_FIELD_RE = re.compile(
    r"(?i)\b(cvv|cvc|expiration_date|expiry|password|token)\b(['\"]?\s*[:=]\s*)(['\"]?)[^\s,'\"}]+\3"
)


def mask_number(value):
    """Mask an account/card number, keeping only the last 4 digits."""
    if value is None:
        return REDACTED
    digits = str(value)
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def redact(text):
    """Scrub card/account numbers, emails and secret key/value pairs from free text."""
    text = _SECRET_FIELD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", str(text))
    text = _EMAIL_RE.sub(REDACTED, text)
    return _LONG_DIGIT_RUN_RE.sub(REDACTED, text)


class RedactingFilter(logging.Filter):
    """Logging filter that redacts PII from the fully formatted message."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def get_redacted_logger(name):
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
