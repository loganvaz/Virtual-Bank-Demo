import logging

from .serializers import AccountSerializer, AccountCreateSerializer
from .models import Account
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework import exceptions
from debit_cards.models import DebitCard
from debit_cards.serializers import generate_cvv, generate_valid_credit_card_number
import datetime
from notifications.utils import process_notifications

from rest_framework import permissions

logger = logging.getLogger(__name__)


def log_rejected(event, user, reason, **fields):
    logger.warning("%s.rejected actor_id=%s reason=%r %s", event, getattr(user, "pk", None), reason, " ".join(f"{k}={v}" for k, v in fields.items()))


class AccountList(generics.ListCreateAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAdminUser]


class AccountDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAdminUser]


class AccountCreate(generics.CreateAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        account_name = self.request.data.get("name")
        account_type = self.request.data.get("account_type")

        existing_account = Account.objects.filter(user=self.request.user, name=account_name).exists()
        if existing_account:
            log_rejected("account.create", self.request.user, "duplicate_name")
            raise exceptions.PermissionDenied('Account with this name already exists')

        account = serializer.save(user=self.request.user)
        logger.info(
            "account.created actor_id=%s account_id=%s account_type=%s currency=%s",
            self.request.user.pk, account.pk, account.account_type, account.currency,
        )

        # notification
        notification_message  = 'A new Account has been successfully created.'
        process_notifications(self.request.user, 'account_notification', notification_message)

        if account_type == "CURRENT":
            debit_card = DebitCard(account=account)
            card_number = generate_valid_credit_card_number()
            expiry_date = datetime.datetime.now() + datetime.timedelta(days=365 * 3)
            debit_card.card_number = card_number
            debit_card.cvv = generate_cvv(card_number, expiry_date)
            debit_card.expiration_date = expiry_date
            debit_card.save()
            logger.info(
                "account.debit_card.issued actor_id=%s account_id=%s card_id=%s",
                self.request.user.pk, account.pk, debit_card.pk,
            )

             # notification
            notification_message = f'A debit card has been successfully created for your account ({account.number}).'
            process_notifications(self.request.user, 'account_notification', notification_message)


class UserAccountList(generics.ListAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return Account.objects.filter(user=user)


class UserAccountDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'number'

    def get_object(self):
        user = self.request.user
        account = Account.objects.filter(user=user, number=self.kwargs['number']).first()
        if not account:
            log_rejected("account.detail", user, "not_found_or_not_owner")
            raise exceptions.NotFound("Not found")

        return account

    def perform_update(self, serializer):
        serializer.validated_data.pop('account_type', None)
        serializer.validated_data.pop('balance', None)
        serializer.validated_data.pop('currency', None)
        account_name = serializer.validated_data.get('name')
        existing_account = Account.objects.filter(user=self.request.user, name=account_name).exclude(pk=serializer.instance.pk).exists()
        if existing_account:
            log_rejected("account.update", self.request.user, "duplicate_name", account_id=serializer.instance.pk)
            raise exceptions.PermissionDenied('Account with this name already exists for the user')

        serializer.save(user=self.request.user)
        logger.info("account.updated actor_id=%s account_id=%s", self.request.user.pk, serializer.instance.pk)

        # notification
        notification_message = 'Account updated successfully'
        process_notifications(self.request.user, 'account_notification', notification_message)

    def perform_destroy(self, instance):
        account_pk = instance.pk
        instance.delete()
        logger.info("account.deleted actor_id=%s account_id=%s", self.request.user.pk, account_pk)

        # notification
        notification_message  = 'Account deleted successfully'
        process_notifications(self.request.user, 'account_notification', notification_message)