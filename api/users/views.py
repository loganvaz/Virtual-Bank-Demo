from .serializers import UserSerializer
from .models import User
from rest_framework import permissions, generics, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from notifications.utils import process_notifications
from .utils import get_client_ip
from django.conf import settings
import logging

logger = logging.getLogger("virtual_bank.auth")

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from dotenv import load_dotenv

load_dotenv()


class UserList(generics.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

    def create(self, request, *args, **kwargs):
        user_ip = get_client_ip(request)

        request.data["ip_address"] = user_ip
        response = super().create(request, *args, **kwargs)

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
            return Response(
                {"error": "password required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(password):
            raise AuthenticationFailed("Invalid password")

        # notification
        notification_message = "Your profile information has been successfully updated. Your changes are now reflected in your profile."
        process_notifications("admin", "user_notification", notification_message)

        return super().update(request, *args, **kwargs)


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
        serializer.save(ip_address=self.user_ip)

        # notification
        notification_message = f"{serializer.validated_data['first_name']} {serializer.validated_data['last_name']} has joined the system"
        process_notifications("admin", "user_notification", notification_message)


def set_auth_cookies(response, access_token, refresh_token):
    flags = {
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
    }
    response.set_cookie("vb_token", access_token, **flags)
    response.set_cookie("vb_rtoken", refresh_token, **flags)


class Login(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        ip = get_client_ip(request)
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            logger.warning("auth.login.failed ip=%s", ip)
            raise

        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")

        if access_token:
            set_auth_cookies(response, access_token, refresh_token)
            logger.info(
                "auth.login.success user_id=%s ip=%s",
                RefreshToken(refresh_token)["user_id"],
                ip,
            )

        return response


class RefreshTokenView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")

        if access_token:
            set_auth_cookies(response, access_token, refresh_token)

        return response


class Logout(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        refresh_token = request.COOKIES.get("vb_rtoken")
        try:
            if not refresh_token:
                raise TokenError("missing refresh token")
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            logger.warning("auth.logout.failed user_id=%s", request.user.id)
            return Response({"details": "failed"}, status=status.HTTP_400_BAD_REQUEST)

        logger.info("auth.logout.success user_id=%s", request.user.id)
        response = Response({"details": "success"})
        response.delete_cookie("vb_token")
        response.delete_cookie("vb_rtoken")
        return response
