from __future__ import annotations

import ipaddress
import os
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests
import urllib3
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers


OUTBOUND_ALLOW_HOSTS_ENV = "DATAMID_OUTBOUND_ALLOW_HOSTS"
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_FORBIDDEN_REQUEST_HEADERS = {
    "host",
    "proxy-authorization",
    "proxy-connection",
}
_KNOWN_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class OutboundRequestBlocked(ValueError):
    """Raised when an outbound request violates the egress policy."""


@dataclass(frozen=True)
class ValidatedOutboundTarget:
    url: str
    hostname: str
    port: int
    is_allowlisted: bool
    resolved_addresses: tuple[str, ...]


def _normalize_hostname(raw_hostname: str) -> str:
    hostname = str(raw_hostname or "").strip().rstrip(".")
    if not hostname or "%" in hostname:
        raise OutboundRequestBlocked("Outbound URL has an invalid hostname")
    try:
        return ipaddress.ip_address(hostname).compressed.lower()
    except ValueError:
        pass
    try:
        normalized = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise OutboundRequestBlocked("Outbound URL has an invalid hostname") from exc
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split(".")):
        raise OutboundRequestBlocked("Outbound URL has an invalid hostname")
    return normalized


def load_exact_host_allowlist(raw_value: str | None = None) -> frozenset[str]:
    """Load an exact-host allowlist. Wildcards, CIDRs, URLs and ports are rejected."""

    raw = os.getenv(OUTBOUND_ALLOW_HOSTS_ENV, "") if raw_value is None else raw_value
    hosts: set[str] = set()
    for raw_entry in str(raw or "").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if any(token in entry for token in ("*", "/", "://")) or entry.startswith("."):
            raise OutboundRequestBlocked(
                f"{OUTBOUND_ALLOW_HOSTS_ENV} only accepts comma-separated exact hostnames or IP addresses"
            )
        if entry.startswith("[") and entry.endswith("]"):
            entry = entry[1:-1]
        else:
            try:
                ipaddress.ip_address(entry)
            except ValueError:
                if ":" in entry:
                    raise OutboundRequestBlocked(
                        f"{OUTBOUND_ALLOW_HOSTS_ENV} entries must not include ports"
                    )
        hosts.add(_normalize_hostname(entry))
    return frozenset(hosts)


def _resolve_addresses(hostname: str, port: int) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundRequestBlocked("Outbound URL hostname could not be resolved") from exc
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
        except (ValueError, IndexError, TypeError):
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        addresses.add(address)
    if not addresses:
        raise OutboundRequestBlocked("Outbound URL hostname did not resolve to an IP address")
    return tuple(sorted(addresses, key=lambda item: (item.version, int(item))))


def _is_forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # is_global also excludes loopback, private, link-local, multicast, reserved,
    # unspecified, documentation and shared carrier-grade NAT ranges.
    return address in _KNOWN_METADATA_ADDRESSES or not address.is_global


def validate_outbound_url(
    url: str,
    *,
    allow_hosts: frozenset[str] | None = None,
) -> ValidatedOutboundTarget:
    raw_url = str(url or "").strip()
    if not raw_url or any(ord(character) < 32 for character in raw_url):
        raise OutboundRequestBlocked("Outbound URL is empty or malformed")
    try:
        parsed = urlsplit(raw_url)
        hostname = _normalize_hostname(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise OutboundRequestBlocked("Outbound URL is malformed") from exc
    if parsed.username is not None or parsed.password is not None:
        raise OutboundRequestBlocked("Outbound URL must not contain embedded credentials")
    if parsed.fragment:
        raise OutboundRequestBlocked("Outbound URL must not contain a fragment")
    if not 1 <= port <= 65535:
        raise OutboundRequestBlocked("Outbound URL has an invalid port")

    scheme = parsed.scheme.lower()
    exact_hosts = load_exact_host_allowlist() if allow_hosts is None else allow_hosts
    is_allowlisted = hostname in exact_hosts
    if scheme != "https" and not (scheme == "http" and is_allowlisted):
        raise OutboundRequestBlocked(
            f"Outbound requests require HTTPS; trusted HTTP hosts must be explicitly listed in {OUTBOUND_ALLOW_HOSTS_ENV}"
        )

    addresses = _resolve_addresses(hostname, port)
    if not is_allowlisted:
        forbidden = [str(address) for address in addresses if _is_forbidden_address(address)]
        if forbidden:
            raise OutboundRequestBlocked(
                "Outbound URL resolves to a non-public or reserved address; use an exact host allowlist entry only for a trusted endpoint"
            )
    return ValidatedOutboundTarget(
        url=raw_url,
        hostname=hostname,
        port=port,
        is_allowlisted=is_allowlisted,
        resolved_addresses=tuple(str(address) for address in addresses),
    )


def _validate_request_headers(headers: Mapping[str, Any] | None) -> None:
    for raw_name in (headers or {}).keys():
        if str(raw_name).strip().lower() in _FORBIDDEN_REQUEST_HEADERS:
            raise OutboundRequestBlocked(f"Outbound request header is not allowed: {raw_name}")


def _host_header(target: ValidatedOutboundTarget, scheme: str) -> str:
    host = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
    default_port = 443 if scheme == "https" else 80
    return host if target.port == default_port else f"{host}:{target.port}"


def _send_to_validated_address(
    *,
    address: str,
    target: ValidatedOutboundTarget,
    prepared_request: requests.PreparedRequest,
    verify_tls: bool,
    timeout: float,
) -> requests.Response:
    scheme = urlsplit(prepared_request.url or target.url).scheme.lower()
    headers = dict(prepared_request.headers)
    headers["Host"] = _host_header(target, scheme)
    if scheme == "https":
        pool: urllib3.HTTPConnectionPool = urllib3.HTTPSConnectionPool(
            address,
            port=target.port,
            timeout=timeout,
            server_hostname=target.hostname,
            assert_hostname=target.hostname if verify_tls else False,
            cert_reqs=ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE,
            ca_certs=requests.certs.where() if verify_tls else None,
        )
    else:
        pool = urllib3.HTTPConnectionPool(address, port=target.port, timeout=timeout)
    try:
        raw_response = pool.urlopen(
            method=prepared_request.method or "GET",
            url=prepared_request.path_url,
            body=prepared_request.body,
            headers=headers,
            redirect=False,
            retries=False,
            assert_same_host=False,
            preload_content=True,
            decode_content=True,
            timeout=timeout,
        )
        response = requests.Response()
        response.status_code = int(raw_response.status)
        response.headers = CaseInsensitiveDict(raw_response.headers)
        response._content = raw_response.data
        response._content_consumed = True
        response.url = prepared_request.url or target.url
        response.reason = raw_response.reason
        response.request = prepared_request
        response.raw = raw_response
        response.encoding = get_encoding_from_headers(response.headers)
        return response
    finally:
        # Responses are preloaded before returning, so the network pool can be
        # closed immediately and cannot perform a later DNS lookup or redirect.
        pool.close()


def safe_outbound_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Perform one request pinned to a previously validated IP address."""

    if kwargs.pop("allow_redirects", False):
        raise OutboundRequestBlocked("Outbound redirects cannot be enabled")
    target = validate_outbound_url(url)
    _validate_request_headers(kwargs.get("headers"))
    verify_tls = bool(kwargs.pop("verify", True))
    if not verify_tls and not target.is_allowlisted:
        raise OutboundRequestBlocked(
            f"TLS verification can only be disabled for an exact host in {OUTBOUND_ALLOW_HOSTS_ENV}"
        )
    try:
        timeout = float(kwargs.pop("timeout", 30))
    except (TypeError, ValueError) as exc:
        raise OutboundRequestBlocked("Outbound request timeout must be a positive number") from exc
    if timeout <= 0:
        raise OutboundRequestBlocked("Outbound request timeout must be a positive number")
    supported_keys = {"headers", "params", "json", "data"}
    unsupported_keys = sorted(set(kwargs) - supported_keys)
    if unsupported_keys:
        raise OutboundRequestBlocked(f"Unsupported outbound request options: {', '.join(unsupported_keys)}")

    prepared_request = requests.Request(method=method, url=target.url, **kwargs).prepare()
    prepared_url = urlsplit(prepared_request.url or "")
    prepared_host = _normalize_hostname(prepared_url.hostname or "")
    prepared_port = prepared_url.port or (443 if prepared_url.scheme.lower() == "https" else 80)
    if prepared_host != target.hostname or prepared_port != target.port:
        raise OutboundRequestBlocked("Prepared outbound request target differs from the validated URL")

    # Connect to the validated IP directly while preserving the original Host,
    # TLS SNI and certificate hostname. This closes the DNS-rebinding gap between
    # policy validation and socket connection. All validated addresses share one
    # total timeout budget, and no proxy or redirect path is involved.
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    response: requests.Response | None = None
    for address in target.resolved_addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = _send_to_validated_address(
                address=address,
                target=target,
                prepared_request=prepared_request,
                verify_tls=verify_tls,
                timeout=remaining,
            )
            break
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            last_error = exc
    if response is None:
        raise requests.RequestException("Outbound request failed for all validated addresses") from last_error
    if response.status_code in _REDIRECT_STATUS_CODES:
        response.close()
        raise OutboundRequestBlocked("Outbound redirects are disabled; configure the final HTTPS URL directly")
    return response
