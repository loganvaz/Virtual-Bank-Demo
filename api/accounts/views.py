from .serializers import AccountSerializer, AccountCreateSerializer
from .models import Account
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework import exceptions
from debit_cards.models import DebitCard
from debit_cards.serializers import generate_cvv, generate_valid_credit_card_number
import datetime
from django.db import transaction as db_transaction
from notifications.utils import process_notifications
from virtual_bank.logging import get_logger

from rest_framework import permissions

logger = get_logger("accounts")
security_logger = get_logger("security")


def _reject(request, event, reason, exc_class, detail, security=False, **fields):
    log = security_logger if security else logger
    log.warning(
        "%s rejected: %s",
        event,
        reason,
        extra={"event": f"{event}.rejected", "reason": reason, "user_id": request.user.pk, **fields},
    )
    raise exc_class(detail)


class AccountList(generics.ListCreateAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_create(self, serializer):
        account = serializer.save()
        logger.info(
            "admin created account",
            extra={
                "event": "account.admin_created",
                "user_id": self.request.user.pk,
                "account_id": account.pk,
                "currency": account.currency,
            },
        )


class AccountDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAdminUser]


class AccountCreate(generics.CreateAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        account_name = serializer.validated_data.get("name")
        account_type = serializer.validated_data.get("account_type", "SAVINGS")

        if Account.objects.filter(user=self.request.user, name=account_name).exists():
            _reject(
                self.request,
                "account.create",
                "duplicate_name",
                exceptions.PermissionDenied,
                "Account with this name already exists",
            )

        with db_transaction.atomic():
            account = serializer.save(user=self.request.user)
            logger.info(
                "account created",
                extra={
                    "event": "account.created",
                    "user_id": self.request.user.pk,
                    "account_id": account.pk,
                    "currency": account.currency,
                },
            )

            # notification
            notification_message = 'A new Account has been successfully created.'
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
                    "debit card issued for new current account",
                    extra={
                        "event": "account.debit_card_issued",
                        "user_id": self.request.user.pk,
                        "account_id": account.pk,
                    },
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
            _reject(
                self.request,
                "account.access",
                "not_found_or_not_owner",
                exceptions.NotFound,
                "Not found",
                security=True,
            )

        return account

    def perform_update(self, serializer):
        serializer.validated_data.pop('account_type', None)
        serializer.validated_data.pop('balance', None)
        serializer.validated_data.pop('currency', None)
        account_name = serializer.validated_data.get('name')
        existing_account = (
            Account.objects.filter(user=self.request.user, name=account_name)
            .exclude(pk=serializer.instance.pk)
            .exists()
        )
        if existing_account:
            _reject(
                self.request,
                "account.update",
                "duplicate_name",
                exceptions.PermissionDenied,
                "Account with this name already exists for the user",
                account_id=serializer.instance.pk,
            )

        account = serializer.save(user=self.request.user)
        logger.info(
            "account updated",
            extra={"event": "account.updated", "user_id": self.request.user.pk, "account_id": account.pk},
        )

        # notification
        notification_message = 'Account updated successfully'
        process_notifications(self.request.user, 'account_notification', notification_message)

    def perform_destroy(self, instance):
        account_id = instance.pk
        instance.delete()
        logger.info(
            "account deleted",
            extra={"event": "account.deleted", "user_id": self.request.user.pk, "account_id": account_id},
        )

        # notification
        notification_message = 'Account deleted successfully'
        process_notifications(self.request.user, 'account_notification', notification_message)
