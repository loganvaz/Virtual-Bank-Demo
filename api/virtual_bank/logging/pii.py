"""
Defense-in-depth PII redaction for log records.

Application code must never log PII directly (log surrogate IDs, amounts and
currency codes only). This filter scrubs anything that slips through so the
log files themselves never contain SSNs, card numbers, account numbers,
emails or phone numbers.
"""

import logging
import re

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
# 13-19 digit runs (optionally space/dash separated) -- candidate card / account numbers
LONG_DIGITS_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# Bare 10-12 digit runs are account numbers in this system
ACCOUNT_RE = re.compile(r"(?<![\w.])\d{10,12}(?![\w.])")

REDACTED = "[REDACTED]"


def _luhn_valid(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_long_digits(match):
    digits = re.sub(r"\D", "", match.group(0))
    if _luhn_valid(digits):
        return f"****{digits[-4:]}"
    return REDACTED


def redact(text):
    """Return ``text`` with all recognised PII patterns masked."""
    if not isinstance(text, str):
        text = str(text)
    text = SSN_RE.sub(REDACTED, text)
    text = EMAIL_RE.sub(REDACTED, text)
    text = PHONE_RE.sub(REDACTED, text)
    text = LONG_DIGITS_RE.sub(_mask_long_digits, text)
    text = ACCOUNT_RE.sub(REDACTED, text)
    return text


def _redact_value(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, int) and not isinstance(value, bool) and len(str(abs(value))) >= 10:
        return redact(str(value))
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


class PIIRedactingFilter(logging.Filter):
    """Scrubs the message, positional args and any ``extra`` attributes."""

    STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))

    def filter(self, record):
        record.msg = _redact_value(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = _redact_value(record.args)
            else:
                record.args = tuple(_redact_value(a) for a in record.args)
        for key, value in list(vars(record).items()):
            if key not in self.STANDARD_ATTRS and key not in ("message", "asctime"):
                setattr(record, key, _redact_value(value))
        return True
