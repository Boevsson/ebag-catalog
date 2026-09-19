from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    `?page=2&page_size=50`. Every list is paginated and the page size is capped,
    so no request can ask the database for the whole catalog at once.

    Page numbers (rather than cursors) because search results can be sorted in
    several ways and clients want to jump to a page. The known trade-off, slow
    very deep pages, is bounded by the cap and acceptable for a catalog.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
