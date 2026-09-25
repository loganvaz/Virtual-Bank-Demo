from .serializers import UserSerializer
from .models import User
from rest_framework import permissions, generics, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from notifications.utils import process_notifications
from virtual_bank.log_redaction import get_redacted_logger
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

logger = get_redacted_logger(__name__)


def _user_id_from_access_token(access_token):
    try:
        return AccessToken(access_token)["user_id"]
    except (TokenError, KeyError):
        return None


class UserList(generics.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]

    def create(self, request, *args, **kwargs):
        user_ip = get_client_ip(request)

        request.data["ip_address"] = user_ip
        response = super().create(request, *args, **kwargs)
        logger.info(
            "User created by admin user_id=%s admin_id=%s",
            response.data.get("id"), request.user.id,
        )

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

    def update(self, request, *args, **kwargs):
        user = self.request.user

        password = request.data.get("password")

        if not (password):
            logger.warning("Profile update rejected: password missing user_id=%s", user.id)
            return Response(
                {"error": "password required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(password):
            logger.warning("Profile update rejected: invalid password user_id=%s", user.id)
            raise AuthenticationFailed("Invalid password")

        # notification
        notification_message = "Your profile information has been successfully updated. Your changes are now reflected in your profile."
        process_notifications("admin", "user_notification", notification_message)

        response = super().update(request, *args, **kwargs)
        logger.info("Profile updated user_id=%s fields=%s", user.id, sorted(request.data.keys()))
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
        serializer.save(ip_address=self.user_ip)
        logger.info("User registered user_id=%s", serializer.instance.id)

        # notification
        notification_message = f"{serializer.validated_data['first_name']} {serializer.validated_data['last_name']} has joined the system"
        process_notifications("admin", "user_notification", notification_message)


class Login(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            logger.warning("Login rejected: invalid credentials")
            raise
        
        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")
        
        if access_token:
            response.set_cookie("vb_token", access_token, httponly=True)
            response.set_cookie("vb_rtoken", refresh_token, httponly=True)
            logger.info("Login succeeded user_id=%s", _user_id_from_access_token(access_token))

        return response


class RefreshTokenView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            logger.warning("Token refresh rejected: invalid or blacklisted refresh token")
            raise
        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")

        if access_token:
            response.set_cookie("vb_token", access_token, httponly=True)
            response.set_cookie("vb_rtoken", refresh_token, httponly=True)
            logger.info("Token refreshed user_id=%s", _user_id_from_access_token(access_token))

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
            logger.info("Logout succeeded user_id=%s", request.user.id)
            
            return response
        except Exception as e:
            logger.warning("Logout failed user_id=%s reason=%s", request.user.id, type(e).__name__)
            return Response({"details": "failed"})
