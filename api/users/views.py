from .serializers import UserSerializer
from .models import User
from rest_framework import permissions, generics, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from notifications.utils import process_notifications
from virtual_bank.logging import get_logger
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

logger = get_logger("users")
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


def _set_token_cookies(response):
    access_token = response.data.get("access")
    refresh_token = response.data.get("refresh")
    if access_token:
        response.set_cookie("vb_token", access_token, httponly=True)
        response.set_cookie("vb_rtoken", refresh_token, httponly=True)


class UserList(generics.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_create(self, serializer):
        user = serializer.save(ip_address=get_client_ip(self.request))
        logger.info("user created by admin", extra={"event": "user.created", "user_id": user.pk})


class UserDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_destroy(self, instance):
        logger.info("user deleted by admin", extra={"event": "user.deleted", "user_id": instance.pk})
        super().perform_destroy(instance)


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
            logger.warning(
                "user.update rejected: password_required",
                extra={"event": "user.update.rejected", "reason": "password_required", "user_id": user.pk},
            )
            return Response(
                {"error": "password required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(password):
            _reject(request, "user.update", "invalid_password", AuthenticationFailed, "Invalid password", security=True)

        response = super().update(request, *args, **kwargs)

        logger.info("user updated", extra={"event": "user.updated", "user_id": user.pk})

        # notification
        notification_message = "Your profile information has been successfully updated. Your changes are now reflected in your profile."
        process_notifications("admin", "user_notification", notification_message)

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
        logger.info("user registered", extra={"event": "user.registered", "user_id": user.pk})

        # notification
        notification_message = f"{user.first_name} {user.last_name} has joined the system".strip()
        process_notifications("admin", "user_notification", notification_message)


class Login(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            security_logger.warning(
                "login rejected: invalid_credentials",
                extra={"event": "login.rejected", "reason": "invalid_credentials"},
            )
            raise

        _set_token_cookies(response)
        logger.info("user logged in", extra={"event": "login.succeeded"})

        return response


class RefreshTokenView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        _set_token_cookies(response)
        logger.info("token refreshed", extra={"event": "token.refreshed"})

        return response


class Logout(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        refresh_token = request.COOKIES.get("vb_rtoken")
        try:
            if not refresh_token:
                raise TokenError("missing refresh token")
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            security_logger.warning(
                "logout rejected: invalid_refresh_token",
                extra={"event": "logout.rejected", "reason": "invalid_refresh_token", "user_id": request.user.pk},
            )
            return Response({"details": "failed"}, status=status.HTTP_400_BAD_REQUEST)

        logger.info("user logged out", extra={"event": "logout.succeeded", "user_id": request.user.pk})
        response = Response({"details": "success"})
        response.delete_cookie('vb_token')
        response.delete_cookie('vb_rtoken')
        return response