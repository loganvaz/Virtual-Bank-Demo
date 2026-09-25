from django.shortcuts import render
from .serializers import AccountSerializer, AccountCreateSerializer
from .models import Account
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework import exceptions
from debit_cards.models import DebitCard
from debit_cards.serializers import generate_cvv, generate_valid_credit_card_number
import datetime
from notifications.utils import process_notifications
from virtual_bank.log_redaction import get_redacted_logger, mask_number

from rest_framework import permissions

logger = get_redacted_logger(__name__)


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
            logger.warning(
                "Account creation rejected: duplicate name user_id=%s account_type=%s",
                self.request.user.id, account_type,
            )
            raise exceptions.PermissionDenied('Account with this name already exists')

        account = serializer.save(user=self.request.user)
        logger.info(
            "Account created account_id=%s user_id=%s account=%s account_type=%s currency=%s",
            account.id, self.request.user.id, mask_number(account.number),
            account.account_type, account.currency,
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
                "Debit card issued card_id=%s account_id=%s user_id=%s card=%s",
                debit_card.id, account.id, self.request.user.id, mask_number(card_number),
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
            logger.warning(
                "Account lookup rejected: not found or not owned user_id=%s account=%s",
                user.id, mask_number(self.kwargs['number']),
            )
            raise exceptions.NotFound("Not found")

        return account

    def perform_update(self, serializer):
        serializer.validated_data.pop('account_type', None)
        serializer.validated_data.pop('balance', None)
        serializer.validated_data.pop('currency', None)
        account_name = serializer.validated_data.get('name')
        existing_account = Account.objects.filter(user=self.request.user, name=account_name).exists()
        if existing_account:
            logger.warning(
                "Account update rejected: duplicate name account_id=%s user_id=%s",
                serializer.instance.id, self.request.user.id,
            )
            raise exceptions.PermissionDenied('Account with this name already exists for the user')

        serializer.save(user=self.request.user)
        logger.info(
            "Account updated account_id=%s user_id=%s account=%s",
            serializer.instance.id, self.request.user.id, mask_number(serializer.instance.number),
        )

        # notification
        notification_message = 'Account updated successfully'
        process_notifications(self.request.user, 'account_notification', notification_message)

    def perform_destroy(self, instance):
        account_id, number = instance.id, instance.number
        instance.delete()
        logger.info(
            "Account deleted account_id=%s user_id=%s account=%s",
            account_id, self.request.user.id, mask_number(number),
        )

        # notification
        notification_message  = 'Account deleted successfully'
        process_notifications(self.request.user, 'account_notification', notification_message)