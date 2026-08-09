import json

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from identity.exceptions import IdentityRateLimited, IdentityUnavailable
from identity.services.authentication import valid_preauthentication, verify_owner_password

MAX_REQUEST_BYTES = 8192


def _require_feature() -> None:
    if not settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED:
        raise Http404


def _problem(
    request: HttpRequest,
    *,
    status: int,
    code: str,
    title: str,
    message: str,
) -> JsonResponse:
    response = JsonResponse(
        {
            "type": f"https://civicloop.karthikkannan.ca/problems/{code}",
            "title": title,
            "status": status,
            "detail": message,
            "instance": request.path,
            "code": code,
            "message": message,
        },
        status=status,
        content_type="application/problem+json",
    )
    response["Cache-Control"] = "no-store"
    return response


def _json_body(request: HttpRequest) -> dict[str, object] | None:
    if len(request.body) > MAX_REQUEST_BYTES:
        return None
    try:
        value = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


@require_GET
@ensure_csrf_cookie
def security_status(request: HttpRequest) -> JsonResponse:
    _require_feature()
    preauth = valid_preauthentication(request)
    if preauth is None:
        return JsonResponse({"stage": "anonymous"}, headers={"Cache-Control": "no-store"})
    return JsonResponse(
        {"stage": preauth["stage"]},
        headers={"Cache-Control": "no-store"},
    )


@require_POST
def password_challenge(request: HttpRequest) -> JsonResponse:
    _require_feature()
    body = _json_body(request)
    username = body.get("username") if body is not None else None
    password = body.get("password") if body is not None else None
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not 1 <= len(username.strip()) <= 150
        or not 1 <= len(password) <= 4096
    ):
        return _problem(
            request,
            status=400,
            code="invalid_request",
            title="Invalid request",
            message="The authentication request is invalid.",
        )
    try:
        challenge = verify_owner_password(
            request,
            username=username,
            password=password,
        )
    except IdentityRateLimited as exc:
        response = _problem(
            request,
            status=429,
            code="rate_limited",
            title="Too many attempts",
            message="Too many authentication attempts. Try again later.",
        )
        response["Retry-After"] = str(exc.retry_after_seconds)
        return response
    except IdentityUnavailable:
        return _problem(
            request,
            status=503,
            code="identity_unavailable",
            title="Authentication unavailable",
            message="Administrator authentication is temporarily unavailable.",
        )
    if challenge is None:
        return _problem(
            request,
            status=401,
            code="invalid_credentials",
            title="Authentication failed",
            message="The supplied credentials are invalid.",
        )
    return JsonResponse(
        {
            "stage": "password_verified",
            "next_action": challenge.next_action,
        },
        headers={"Cache-Control": "no-store"},
    )
