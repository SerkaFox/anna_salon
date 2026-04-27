from rest_framework.permissions import IsAuthenticated


class IsAuthenticatedMobileUser(IsAuthenticated):
    """Named permission for the mobile API default contract."""

