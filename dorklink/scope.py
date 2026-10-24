from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class ScopeError(ValueError):
    pass


def normalize_target(value: str) -> str:
    value = value.strip()
    if not value:
        raise ScopeError("Enter a target website domain or URL.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ScopeError("Target must be a valid HTTP or HTTPS website.")
    if parsed.username or parsed.password:
        raise ScopeError("Credentials are not allowed in the target URL.")
    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_loopback or address.is_unspecified or address.is_private or address.is_link_local):
        raise ScopeError("Private, loopback, and link-local IP targets are not supported.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ScopeError("Local network hostnames are not supported.")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ScopeError("Target URL has an invalid port.") from exc
    rendered_host = f"[{host}]" if ":" in host else host
    port = f":{parsed_port}" if parsed_port else ""
    return f"{parsed.scheme.lower()}://{rendered_host}{port}/"


def host_in_scope(url: str, target_host: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    root = target_host.lower().rstrip(".")
    return bool(host) and (host == root or host.endswith("." + root))


def safe_http_url(url: str, target_host: str) -> bool:
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.username is None and parsed.password is None and host_in_scope(url, target_host)
