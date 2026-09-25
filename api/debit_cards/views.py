from .serializers import DebitCardSerializer
from .models import DebitCard
from rest_framework import generics
from rest_framework import exceptions
from rest_framework import permissions
from virtual_bank.logging import get_logger

logger = get_logger("debit_cards")
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


def _card_fields(card):
    return {"card_id": card.pk, "account_id": card.account_id, "user_id": card.account.user_id}


class DebitCardList(generics.ListCreateAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_create(self, serializer):
        card = serializer.save()
        logger.info("debit card created", extra={"event": "card.created", **_card_fields(card)})


class DebitCardDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_update(self, serializer):
        card = serializer.save()
        logger.info("debit card updated", extra={"event": "card.updated", **_card_fields(card)})

    def perform_destroy(self, instance):
        fields = _card_fields(instance)
        instance.delete()
        logger.info("debit card deleted", extra={"event": "card.deleted", **fields})


class UserDebitCardList(generics.ListAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return DebitCard.objects.filter(account__user=user)


class UserDebitCardDetail(generics.RetrieveAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        user = self.request.user
        number = self.kwargs["number"]

        debit_card = DebitCard.objects.filter(card_number=number, account__user=user).first()

        if not debit_card:
            _reject(self.request, "card_lookup", "card_not_found", exceptions.NotFound,
                    "Not found.", security=True)

        return debit_card
