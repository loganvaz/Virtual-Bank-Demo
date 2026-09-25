import logging

from .serializers import UserSerializer
from .models import User
from rest_framework import permissions, generics, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from notifications.utils import process_notifications
from .utils import get_client_ip
import datetime
import os

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")


def log_rejected(event, user, reason, **fields):
    """Audit a rejected operation. Only surrogate IDs are logged."""
    logger.warning(
        "%s.rejected actor_id=%s reason=%r %s",
        event,
        getattr(user, "pk", None),
        reason,
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


def log_event(event, user, **fields):
    logger.info(
        "%s actor_id=%s %s",
        event,
        getattr(user, "pk", None),
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


class UserList(generics.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

    def create(self, request, *args, **kwargs):
        user_ip = get_client_ip(request)

        request.data["ip_address"] = user_ip
        response = super().create(request, *args, **kwargs)
        log_event("admin.user.created", request.user, user_id=response.data.get("id"))

        return response


class UserDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

class UserGet(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class UserUpdate(generics.UpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        user = self.request.user

        password = request.data.get("password")

        if not (password):
            log_rejected("user.update", user, "password_required")
            return Response(
                {"error": "password required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(password):
            security_logger.warning("user.update.rejected actor_id=%s reason='invalid_password'", user.pk)
            raise AuthenticationFailed("Invalid password")

        # notification
        notification_message = "Your profile information has been successfully updated. Your changes are now reflected in your profile."
        process_notifications("admin", "user_notification", notification_message)

        response = super().update(request, *args, **kwargs)
        log_event("user.updated", user, fields=sorted(k for k in request.data.keys() if k != "password"))
        return response


class UserCreate(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    user_ip = None

    def create(self, request, *args, **kwargs):
        self.user_ip = get_client_ip(request)
        response = super().create(request, *args, **kwargs)

        return response

    def perform_create(self, serializer):
        user = serializer.save(ip_address=self.user_ip)
        log_event("user.registered", None, user_id=user.pk)

        # notification
        notification_message = f"{user.first_name} {user.last_name} has joined the system".strip()
        process_notifications("admin", "user_notification", notification_message)


class Login(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            security_logger.warning("auth.login.failed reason='invalid_credentials'")
            raise

        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")

        if access_token:
            response.set_cookie("vb_token", access_token, httponly=True)
            response.set_cookie("vb_rtoken", refresh_token, httponly=True)
            security_logger.info(
                "auth.login.succeeded user_id=%s",
                User.objects.filter(username=request.data.get("username")).values_list("pk", flat=True).first(),
            )

        return response


class RefreshTokenView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            security_logger.warning("auth.refresh.failed reason='invalid_token'")
            raise
        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")

        if access_token:
            response.set_cookie("vb_token", access_token, httponly=True)
            response.set_cookie("vb_rtoken", refresh_token, httponly=True)
            security_logger.info("auth.refresh.succeeded")

        return response


class Logout(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            refresh_token = request.COOKIES.get("vb_rtoken")
            token = RefreshToken(refresh_token)
            token.blacklist()
            
            response = Response({"details": "success"})
            
            response.delete_cookie('vb_token')
            response.delete_cookie('vb_rtoken')

            security_logger.info("auth.logout.succeeded user_id=%s", request.user.pk)
            return response
        except Exception as e:
            security_logger.warning(
                "auth.logout.failed user_id=%s reason=%s", request.user.pk, type(e).__name__
            )
            return Response({"details": "failed"})
