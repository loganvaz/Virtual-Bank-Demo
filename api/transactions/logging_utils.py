import logging

logger = logging.getLogger("transactions")


def mask_account(number):
    """Return only the last 4 digits of an account/card number for safe logging."""
    digits = str(number)
    return f"****{digits[-4:]}" if digits else "****"


def log_transaction(event, user, **fields):
    """Log a transaction event with a PII-free payload (user id + masked numbers only)."""
    payload = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("%s user_id=%s %s", event, getattr(user, "id", None), payload)
