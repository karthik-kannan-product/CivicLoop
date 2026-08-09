from ipaddress import ip_address

from django.conf import settings
from django.http import HttpRequest


def _normalized_ip(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or "," in value:
        return None
    try:
        return ip_address(value).compressed
    except ValueError:
        return None


def source_ip(request: HttpRequest) -> str | None:
    direct_peer = _normalized_ip(request.META.get("REMOTE_ADDR"))
    if direct_peer is None:
        return None
    if direct_peer not in settings.ADMIN_TRUSTED_PROXY_IPS:
        return direct_peer
    return _normalized_ip(request.META.get("HTTP_X_FORWARDED_FOR"))
