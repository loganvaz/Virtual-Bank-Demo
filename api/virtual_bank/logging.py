"""Shared structured logging with PII redaction.

Attach ``PIIRedactingFilter`` to a *handler* so every logger routed through it
(app loggers, ``django.request``, third-party) is redacted, regardless of what
the caller passed in the message, ``args`` or ``extra``.
"""
import json
import logging
import re

STRUCTURED_FIELDS = (
    "event",
    "reason",
    "user_id",
    "account_id",
    "payee_account_id",
    "txn_id",
    "txn_type",
    "amount",
    "currency",
    "currency_sent",
    "currency_received",
    "rate",
    "status_code",
)

_LUHN_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def luhn_valid(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_cards(text):
    def repl(match):
        digits = re.sub(r"[ -]", "", match.group(0))
        return "[CARD-REDACTED]" if luhn_valid(digits) else match.group(0)

    return _LUHN_CANDIDATE.sub(repl, text)


# Order matters: more specific patterns first.
_PATTERNS = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN-REDACTED]"),
    (re.compile(r"(?i)\b(ssn|social)\W{0,3}(\d{3}[ -]?\d{2}[ -]?\d{4})\b"), r"\1 [SSN-REDACTED]"),
    (re.compile(r"(?i)\bcvv\W{0,3}\d{3,4}\b"), "cvv=[REDACTED]"),
    (re.compile(r"\b(0[1-9]|1[0-2])/\d{2}\b"), "[EXP-REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL-REDACTED]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP-REDACTED]"),
    (re.compile(r"\b\d{10}\b"), "[ACCT-REDACTED]"),
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), None),  # phone: gated on digit count below
)


def redact(text):
    if not isinstance(text, str):
        return text
    text = _redact_cards(text)
    for pattern, replacement in _PATTERNS:
        if replacement is None:
            text = pattern.sub(
                lambda m: "[PHONE-REDACTED]" if len(re.sub(r"\D", "", m.group(0))) >= 10 else m.group(0),
                text,
            )
        else:
            text = pattern.sub(replacement, text)
    return text


class PIIRedactingFilter(logging.Filter):
    """Handler-level filter that masks SSNs, card/account numbers, CVVs, emails, phones, IPs and tokens in every record."""

    def filter(self, record):
        record.msg = redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact(v) for k, v in record.args.items()}
            else:
                record.args = tuple(redact(a) for a in record.args)
        for field in STRUCTURED_FIELDS:
            if hasattr(record, field):
                setattr(record, field, redact(getattr(record, field)))
        return True


class JsonFormatter(logging.Formatter):
    """Formats records as one JSON object per line, including the structured `extra` fields."""

    def format(self, record):
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in STRUCTURED_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name):
    return logging.getLogger(name)


def build_logging_config(level="INFO"):
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"redact_pii": {"()": "virtual_bank.logging.PIIRedactingFilter"}},
        "formatters": {"json": {"()": "virtual_bank.logging.JsonFormatter"}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "filters": ["redact_pii"],
            }
        },
        "root": {"handlers": ["console"], "level": "WARNING"},
        "loggers": {
            "transactions": {"handlers": ["console"], "level": level, "propagate": False},
            "accounts": {"handlers": ["console"], "level": level, "propagate": False},
            "security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
            "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        },
    }
