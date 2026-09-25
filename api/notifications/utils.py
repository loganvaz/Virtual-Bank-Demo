from .models import Notification
from users.models import User
from virtual_bank.logging import get_logger

logger = get_logger("notifications")

NOTIFICATION_TYPES = (
    "user_notification",
    "account_notification",
    "transaction_notification",
    "security_notification",
)


def process_notifications(user, type, message):
    """
    Process notifications based on type and send to relevant users.

    Args:
    - user (User): The user receiving the notification.
    - type (str): The type of notification to be processed.
    - message (str): The content of the notification.

    Raises:
    - ValueError: If an unknown notification type is encountered.
    """
    # Check if the notification type is valid
    if type not in NOTIFICATION_TYPES:
        logger.warning(
            "notification rejected: unknown_type",
            extra={
                "event": "notification.rejected",
                "reason": "unknown_type",
                "user_id": None if user == 'admin' else user.pk,
            },
        )
        raise ValueError('Unknown notification type')

    # Determine users to notify based on the input 'user'
    users_to_notify = User.objects.filter(is_superuser=True) if user == 'admin' else [user]

    # Iterate through users to notify and send appropriate notifications
    for target_user in users_to_notify:
        if type == 'user_notification':
            send_user_notification(target_user, message)
        elif type == 'account_notification':
            send_account_notification(target_user, message)
        elif type == 'transaction_notification':
            send_transaction_notification(target_user, message)
        elif type == 'security_notification':
            send_security_notification(target_user, message)


def _create(user, notification_type, message):
    notification = Notification.objects.create(user=user, notification_type=notification_type, content=message)
    logger.info(
        "notification created",
        extra={
            "event": "notification.created",
            "user_id": user.pk,
            "notification_id": notification.pk,
            "notification_type": notification_type,
        },
    )
    return notification


def send_user_notification(user, message):
    return _create(user, 'USER_NOTIFICATION', message)

def send_account_notification(user, message):
    return _create(user, 'ACCOUNT_NOTIFICATION', message)

def send_transaction_notification(user, message):
    return _create(user, 'TRANSACTION_NOTIFICATION', message)

def send_security_notification(user, message):
    return _create(user, 'SECURITY_NOTIFICATION', message)
