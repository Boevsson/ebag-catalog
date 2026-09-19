"""
Error responses for requests that never reach a DRF view.

DRF answers errors inside its views with JSON (`{"detail": ...}`). A URL that
matches no route, a request Django refuses as malformed, or an exception that
nobody handled are answered by Django itself - by default with an HTML page.
A JSON API answers JSON, also when the answer is "there is no such URL", which
is the first thing a client sees after a typo (or a trailing slash).

Wired up as `handler400/404/500` in `config/urls.py`. Django only uses them
when `DEBUG` is off; with `DEBUG` on it shows its own debugging pages.
"""

from django.http import HttpRequest, JsonResponse


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse({"detail": "Bad request."}, status=400)


def not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse({"detail": "Not found."}, status=404)


def server_error(request: HttpRequest) -> JsonResponse:
    # The details belong in the log (Django logs the traceback), not in the response.
    return JsonResponse({"detail": "Internal server error."}, status=500)
