from rest_framework import serializers
from .models import Account
from .utils import generate_account_number
from users.models import User
from users.serializers import UserSerializer


class AccountSerializer(serializers.ModelSerializer):
    number = serializers.CharField(read_only=True)
    created_date = serializers.DateTimeField(read_only=True)
    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        source="user", queryset=User.objects.all(), write_only=True, required=False
    )

    class Meta:
        model = Account

        fields = [
            "id",
            "user",
            "user_id",
            "name",
            "account_type",
            "balance",
            "number",
            "currency",
            "created_date",
        ]

    def get_user(self, obj):
        return (f"{obj.user.first_name} {obj.user.last_name}") if obj.user else None

    def create(self, validated_data):
        if "user" not in validated_data:
            raise serializers.ValidationError({"user_id": "This field is required."})
        validated_data["number"] = generate_account_number()
        account = Account.objects.create(**validated_data)
        return account


class AccountCreateSerializer(serializers.ModelSerializer):
    number = serializers.CharField(read_only=True)
    created_date = serializers.DateTimeField(read_only=True)
    user = UserSerializer(read_only=True)
    balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)

    class Meta:
        model = Account
        fields = [
            "id",
            "user",
            "name",
            "account_type",
            "balance",
            "number",
            "currency",
            "created_date",
        ]

        extra_kwargs = {
            "user": {"read_only": True},
        }

    def get_user(self, obj):
        return obj.user.username if obj.user else None

    def create(self, validated_data):
        validated_data["number"] = generate_account_number()
        account = Account.objects.create(**validated_data)
        return account
