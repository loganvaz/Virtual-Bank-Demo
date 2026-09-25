from rest_framework import serializers
from .models import DebitCard
from .utils import generate_valid_credit_card_number, generate_cvv
from accounts.serializers import AccountSerializer
from virtual_bank.log_redaction import get_redacted_logger, mask_number

logger = get_redacted_logger(__name__)


class DebitCardSerializer(serializers.ModelSerializer):
    card_number = serializers.CharField(read_only=True)
    cvv = serializers.CharField(read_only=True)
    created_date = serializers.DateTimeField(read_only=True)
    account = AccountSerializer()
    expiration_date = serializers.SerializerMethodField()

    class Meta:
        model = DebitCard
        fields = ["id", "account", "card_number", "cvv", "expiration_date", "created_date"]

    def create(self, validated_data):
        validated_data["card_number"] = generate_valid_credit_card_number()
        validated_data["cvv"] = generate_cvv(
            validated_data["card_number"], validated_data["expiration_date"]
        )
        credit_card = DebitCard.objects.create(**validated_data)
        logger.info(
            "Debit card issued card_id=%s account_id=%s card=%s",
            credit_card.id, credit_card.account_id, mask_number(credit_card.card_number),
        )
        return credit_card

    def get_expiration_date(self, obj):
        return obj.expiration_date.strftime("%m/%y") if obj.expiration_date else None