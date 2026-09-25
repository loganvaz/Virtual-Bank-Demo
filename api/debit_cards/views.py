from django.shortcuts import render
from .serializers import DebitCardSerializer
from .models import DebitCard
from accounts.models import Account
from rest_framework import generics
from rest_framework import exceptions
from django.db.models import Q
from rest_framework import permissions
from notifications.utils import process_notifications
from django.utils.timezone import localtime
from virtual_bank.log_redaction import get_redacted_logger, mask_number

logger = get_redacted_logger(__name__)


class PermissionLoggingMixin:
    def permission_denied(self, request, message=None, code=None):
        logger.warning(
            "Debit card access denied view=%s user_id=%s authenticated=%s",
            self.__class__.__name__,
            request.user.id,
            request.user.is_authenticated,
        )
        super().permission_denied(request, message=message, code=code)


class DebitCardList(PermissionLoggingMixin, generics.ListCreateAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        card = serializer.instance
        logger.info(
            "Debit card created card_id=%s account_id=%s card=%s admin_id=%s",
            card.id, card.account_id, mask_number(card.card_number), self.request.user.id,
        )


class DebitCardDetail(PermissionLoggingMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_update(self, serializer):
        super().perform_update(serializer)
        card = serializer.instance
        logger.info(
            "Debit card updated card_id=%s account_id=%s card=%s admin_id=%s",
            card.id, card.account_id, mask_number(card.card_number), self.request.user.id,
        )

    def perform_destroy(self, instance):
        logger.info(
            "Debit card deleted card_id=%s account_id=%s card=%s admin_id=%s",
            instance.id, instance.account_id, mask_number(instance.card_number),
            self.request.user.id,
        )
        super().perform_destroy(instance)


class UserDebitCardList(PermissionLoggingMixin, generics.ListAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return DebitCard.objects.filter(account__user=user)


class UserDebitCardDetail(PermissionLoggingMixin, generics.RetrieveAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        user = self.request.user
        number = self.kwargs["number"]

        debit_card = DebitCard.objects.filter(card_number=number, account__user=user).first()


        if not debit_card:
            logger.warning(
                "Debit card lookup rejected: not found for user user_id=%s card=%s",
                user.id, mask_number(number),
            )
            raise exceptions.NotFound()

        return debit_card


# def renewDebitCard():
#     pass
