import logging

from rest_framework import exceptions
from rest_framework import generics
from rest_framework import permissions

from .models import DebitCard
from .serializers import DebitCardSerializer

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")


class DebitCardList(generics.ListCreateAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAdminUser]


class DebitCardDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAdminUser]


class UserDebitCardList(generics.ListAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return DebitCard.objects.filter(account__user=user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        logger.info(
            "debit_card.listed actor_id=%s count=%s",
            request.user.pk,
            len(response.data),
        )
        return response


class UserDebitCardDetail(generics.RetrieveAPIView):
    queryset = DebitCard.objects.all()
    serializer_class = DebitCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        user = self.request.user
        number = self.kwargs["number"]

        debit_card = DebitCard.objects.filter(card_number=number, account__user=user).first()

        if not debit_card:
            security_logger.warning("debit_card.not_found actor_id=%s", user.pk)
            raise exceptions.NotFound()

        logger.info(
            "debit_card.viewed actor_id=%s card_id=%s account_id=%s",
            user.pk,
            debit_card.pk,
            debit_card.account_id,
        )
        return debit_card


# def renewDebitCard():
#     pass
