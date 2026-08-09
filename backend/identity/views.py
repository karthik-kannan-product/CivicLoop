import json
from uuid import UUID

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from identity.exceptions import (
    IdentityCryptoError,
    IdentityError,
    IdentityRateLimited,
    IdentityUnavailable,
)
from identity.services.authentication import (
    complete_recovery_authentication,
    complete_totp_authentication,
    confirm_totp_enrollment,
    enrollment_profile,
    fresh_reauthenticate,
    logout_administrator,
    preauthenticated_profile,
    start_totp_enrollment,
    valid_preauthentication,
    verify_owner_password,
)

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
    metadata = getattr(request, "administrator_session", None)
    if metadata is not None:
        stage = "recovery_restricted" if metadata.recovery_restricted else "authenticated"
        return JsonResponse({"stage": stage}, headers={"Cache-Control": "no-store"})
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
            "expires_at": request.session["civicloop_admin_preauth"]["expires_at"],
            "next_action": challenge.next_action,
        },
        headers={"Cache-Control": "no-store"},
    )


def _preauthentication_problem(request: HttpRequest) -> JsonResponse:
    return _problem(
        request,
        status=401,
        code="preauthentication_required",
        title="Password verification required",
        message="Complete administrator password verification to continue.",
    )


def _verification_problem(request: HttpRequest) -> JsonResponse:
    return _problem(
        request,
        status=401,
        code="verification_failed",
        title="Verification failed",
        message="The supplied verification credential is invalid.",
    )


@require_POST
def totp_challenge(request: HttpRequest) -> JsonResponse:
    _require_feature()
    profile = preauthenticated_profile(request)
    if profile is None:
        return _preauthentication_problem(request)
    body = _json_body(request)
    token = body.get("token") if body is not None else None
    if not isinstance(token, str) or len(token) > 32:
        return _verification_problem(request)
    try:
        complete_totp_authentication(request, profile, token=token)
    except IdentityCryptoError:
        return _problem(
            request,
            status=503,
            code="identity_unavailable",
            title="Authentication unavailable",
            message="Administrator authentication is temporarily unavailable.",
        )
    except IdentityError:
        return _verification_problem(request)
    return JsonResponse({"stage": "authenticated"}, headers={"Cache-Control": "no-store"})


@require_POST
def recovery_challenge(request: HttpRequest) -> JsonResponse:
    _require_feature()
    profile = preauthenticated_profile(request)
    if profile is None:
        return _preauthentication_problem(request)
    body = _json_body(request)
    recovery_code = body.get("recovery_code") if body is not None else None
    if not isinstance(recovery_code, str) or len(recovery_code) > 64:
        return _verification_problem(request)
    try:
        complete_recovery_authentication(
            request,
            profile,
            recovery_code=recovery_code,
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
    except IdentityError:
        return _verification_problem(request)
    return JsonResponse(
        {"stage": "recovery_restricted", "next_action": "replace_totp"},
        headers={"Cache-Control": "no-store"},
    )


@require_POST
def logout(request: HttpRequest) -> JsonResponse:
    _require_feature()
    if not logout_administrator(request):
        return _preauthentication_problem(request)
    return JsonResponse({"stage": "anonymous"}, headers={"Cache-Control": "no-store"})


def _authentication_problem(request: HttpRequest) -> JsonResponse:
    return _problem(
        request,
        status=401,
        code="authentication_required",
        title="Authentication required",
        message="Administrator authentication is required.",
    )


@require_POST
def totp_enrollment(request: HttpRequest) -> JsonResponse:
    _require_feature()
    profile = enrollment_profile(request)
    if profile is None:
        return _authentication_problem(request)
    body = _json_body(request)
    label = body.get("label") if body is not None else None
    if not isinstance(label, str):
        return _problem(
            request,
            status=400,
            code="invalid_request",
            title="Invalid request",
            message="The enrollment request is invalid.",
        )
    try:
        enrollment = start_totp_enrollment(request, profile, label=label)
    except IdentityError:
        return _problem(
            request,
            status=400,
            code="invalid_request",
            title="Invalid request",
            message="The enrollment request is invalid.",
        )
    return JsonResponse(
        {
            "device_id": str(enrollment.device.id),
            "otpauth_uri": enrollment.otpauth_uri,
            "manual_secret": enrollment.manual_secret,
        },
        headers={"Cache-Control": "no-store"},
    )


@require_POST
def totp_confirmation(request: HttpRequest) -> JsonResponse:
    _require_feature()
    profile = enrollment_profile(request)
    if profile is None:
        return _authentication_problem(request)
    body = _json_body(request)
    raw_device_id = body.get("device_id") if body is not None else None
    token = body.get("token") if body is not None else None
    try:
        device_id = UUID(raw_device_id) if isinstance(raw_device_id, str) else None
    except ValueError:
        device_id = None
    if device_id is None or not isinstance(token, str) or len(token) > 32:
        return _verification_problem(request)
    try:
        confirmation = confirm_totp_enrollment(
            request,
            profile,
            device_id=device_id,
            token=token,
        )
    except IdentityCryptoError:
        return _problem(
            request,
            status=503,
            code="identity_unavailable",
            title="Authentication unavailable",
            message="Administrator authentication is temporarily unavailable.",
        )
    except IdentityError:
        return _verification_problem(request)
    return JsonResponse(
        {
            "stage": "authenticated",
            "recovery_codes": list(confirmation.recovery_codes),
        },
        headers={"Cache-Control": "no-store"},
    )


@require_POST
def reauthentication(request: HttpRequest) -> JsonResponse:
    _require_feature()
    metadata = getattr(request, "administrator_session", None)
    if metadata is None or metadata.recovery_restricted:
        return _authentication_problem(request)
    body = _json_body(request)
    password = body.get("password") if body is not None else None
    token = body.get("token") if body is not None else None
    if (
        not isinstance(password, str)
        or not isinstance(token, str)
        or len(password) > 4096
        or len(token) > 32
    ):
        return _verification_problem(request)
    try:
        fresh_reauthenticate(
            request,
            metadata,
            password=password,
            token=token,
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
    except IdentityError:
        return _verification_problem(request)
    return JsonResponse({"fresh": True}, headers={"Cache-Control": "no-store"})
