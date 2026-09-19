from django.urls import path
from rest_framework.routers import SimpleRouter

from catalog.api import views

# No URL of this service ends with a slash. DRF's default (`/products/`) comes
# from Django's HTML heritage; combined with APPEND_SLASH, a client that forgets
# the slash on a POST is redirected and loses its request body. With one
# spelling per URL there is nothing to redirect: the other spelling is a 404.
router = SimpleRouter(trailing_slash=False)
router.register("categories", views.CategoryViewSet, basename="category")
router.register("products", views.ProductViewSet, basename="product")

urlpatterns = [
    path("auth/token", views.ThrottledTokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh", views.ThrottledTokenRefreshView.as_view(), name="token-refresh"),
    *router.urls,
]
