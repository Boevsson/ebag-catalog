from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsStaffOrReadOnly(BasePermission):
    """
    Anyone may read the catalog; only staff may change it.

    `IsAuthenticatedOrReadOnly` would not be enough: a shop's customers have
    accounts too, and being logged in must not allow editing products.
    """

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
