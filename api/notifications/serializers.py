from rest_framework import serializers
from .models import Notification
from users.models import User
from users.serializers import UserSerializer


class NotificationSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    created_date = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "user",
            "notification_type",
            "content",
            "status",
            "created_date",
        ]

        extra_kwargs = {
            "notification_type": {"read_only": True},
            "content": {"read_only": True},
        }


class AdminNotificationSerializer(NotificationSerializer):
    """Admin variant: accepts a target ``user_id`` plus writable type/content so POST does not crash on a NULL user."""

    user_id = serializers.PrimaryKeyRelatedField(
        source="user", queryset=User.objects.all(), write_only=True
    )

    class Meta(NotificationSerializer.Meta):
        fields = NotificationSerializer.Meta.fields + ["user_id"]
        extra_kwargs = {}
