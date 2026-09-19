from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from catalog.api.serializers import (
    CategorySerializer,
    ProductSearchQuerySerializer,
    ProductSerializer,
)
from catalog.models import Category, Product
from catalog.search import search_products
from catalog.services.categories import delete_category

TOP_LEVEL = "root"


class LocationHeaderMixin:
    """A 201 response says where the new resource lives (RFC 9110, section 10.2.2)."""

    def get_success_headers(self, data: dict) -> dict:
        url = reverse(f"{self.basename}-detail", args=[data["id"]], request=self.request)
        return {"Location": url}


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "parent",
                OpenApiTypes.STR,
                description=(
                    "Only the direct children of this category id, "
                    f'or "{TOP_LEVEL}" for the top-level categories.'
                ),
            )
        ]
    )
)
class CategoryViewSet(LocationHeaderMixin, viewsets.ModelViewSet):
    """
    The category tree. Creating, renaming, moving and deleting are delegated to
    `catalog.services.categories`, which keeps the tree consistent.
    """

    serializer_class = CategorySerializer
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        categories = Category.objects.all()
        parent = self.request.query_params.get("parent")
        if self.action != "list" or not parent:
            return categories
        if parent == TOP_LEVEL:
            return categories.filter(parent__isnull=True)
        if not parent.isdecimal():
            raise serializers.ValidationError(
                {"parent": [f'Expected a category id or "{TOP_LEVEL}".']}
            )
        return categories.filter(parent_id=int(parent))

    def perform_destroy(self, instance: Category) -> None:
        delete_category(instance)  # CategoryInUse becomes a 409 in the exception handler


class ProductViewSet(LocationHeaderMixin, viewsets.ModelViewSet):
    """
    Products are plain rows: the serializer validates them and the model and
    database constraints protect them, so there is no service layer in between.
    """

    # select_related: `category_name` would otherwise cost one query per product.
    queryset = Product.objects.select_related("category")
    serializer_class = ProductSerializer
    lookup_value_regex = r"\d+"

    @extend_schema(
        summary="Search and filter products",
        description=(
            "All parameters are optional and combined with AND. "
            "The result is paginated like every other list."
        ),
        parameters=[ProductSearchQuerySerializer],
        responses={
            200: ProductSerializer(many=True),
            400: OpenApiResponse(
                description=(
                    "One or more parameters are invalid. The body names each of "
                    'them: `{"max_price": ["max_price cannot be lower than min_price."]}`'
                )
            ),
        },
    )
    @action(detail=False, methods=["get"])
    def search(self, request: Request) -> Response:
        # HTML forms submit `?q=&min_price=` for inputs left empty: treat as absent.
        given = {name: value for name, value in request.query_params.items() if value != ""}
        query = ProductSearchQuerySerializer(data=given)
        query.is_valid(raise_exception=True)

        products = search_products(query.validated_data["criteria"])

        # Pagination is switched on for the whole API in settings, so `page` is
        # never None: an unpaginated search could return the entire catalog.
        page = self.paginate_queryset(products)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


# --- authentication -------------------------------------------------------------------
# Rate-limited, because these are the endpoints a password-guessing attack would hit.


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_scope = "auth"


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_scope = "auth"
