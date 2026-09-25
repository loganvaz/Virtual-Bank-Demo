import logging
import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                      # emails
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),                     # JWTs
    re.compile(r"(?i)\b(password|token|refresh|access|cvv)\b(\s*[=:]\s*)\S+"),
    re.compile(r"\b\d{13,19}\b"),                                 # card numbers
    re.compile(r"\+?\d[\d\s()-]{8,}\d"),                          # phone numbers (dots excluded so IPs survive)
]


def redact(text):
    text = _PATTERNS[0].sub(REDACTED, text)
    text = _PATTERNS[1].sub(REDACTED, text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _PATTERNS[3].sub(REDACTED, text)
    text = _PATTERNS[4].sub(REDACTED, text)
    return text


class PIIRedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True
