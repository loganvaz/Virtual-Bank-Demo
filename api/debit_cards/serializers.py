from django.utils import timezone
from rest_framework import serializers
from .models import DebitCard
from .utils import generate_valid_credit_card_number, generate_cvv
from accounts.serializers import AccountSerializer
from accounts.models import Account


class DebitCardSerializer(serializers.ModelSerializer):
    card_number = serializers.CharField(read_only=True)
    cvv = serializers.CharField(read_only=True)
    created_date = serializers.DateTimeField(read_only=True)
    account = AccountSerializer(read_only=True)
    account_id = serializers.PrimaryKeyRelatedField(
        source="account", queryset=Account.objects.all(), write_only=True
    )
    expiration_date = serializers.SerializerMethodField()
    expires_at = serializers.DateTimeField(source="expiration_date", write_only=True)

    class Meta:
        model = DebitCard
        fields = ["id", "account", "account_id", "card_number", "cvv", "expiration_date", "expires_at", "created_date"]

    def validate_expires_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Expiration date must be in the future.")
        return value

    def create(self, validated_data):
        validated_data["card_number"] = generate_valid_credit_card_number()
        validated_data["cvv"] = generate_cvv(
            validated_data["card_number"], validated_data["expiration_date"]
        )
        credit_card = DebitCard.objects.create(**validated_data)
        return credit_card

    def get_expiration_date(self, obj):
        return obj.expiration_date.strftime("%m/%y") if obj.expiration_date else None
