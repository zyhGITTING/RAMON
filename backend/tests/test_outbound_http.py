from __future__ import annotations

import socket
import unittest
from unittest.mock import Mock, patch

import requests

from backend.app.core.outbound_http import (
    OutboundRequestBlocked,
    ValidatedOutboundTarget,
    load_exact_host_allowlist,
    safe_outbound_request,
    validate_outbound_url,
)


def _dns_answer(address: str) -> list[tuple]:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    socket_address = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    return [(family, socket.SOCK_STREAM, 6, "", socket_address)]


class OutboundUrlPolicyTests(unittest.TestCase):
    def test_plain_http_is_blocked_by_default(self) -> None:
        with self.assertRaisesRegex(OutboundRequestBlocked, "require HTTPS"):
            validate_outbound_url("http://api.example.com/data", allow_hosts=frozenset())

    @patch("backend.app.core.outbound_http.socket.getaddrinfo")
    def test_private_dns_answer_is_blocked(self, resolver) -> None:
        resolver.return_value = _dns_answer("10.20.30.40")
        with self.assertRaisesRegex(OutboundRequestBlocked, "non-public or reserved"):
            validate_outbound_url("https://api.example.com/data", allow_hosts=frozenset())

    @patch("backend.app.core.outbound_http.socket.getaddrinfo")
    def test_mixed_public_and_private_dns_answers_are_blocked(self, resolver) -> None:
        resolver.return_value = _dns_answer("93.184.216.34") + _dns_answer("127.0.0.1")
        with self.assertRaisesRegex(OutboundRequestBlocked, "non-public or reserved"):
            validate_outbound_url("https://api.example.com/data", allow_hosts=frozenset())

    @patch("backend.app.core.outbound_http.socket.getaddrinfo")
    def test_metadata_address_is_blocked(self, resolver) -> None:
        resolver.return_value = _dns_answer("100.100.100.200")
        with self.assertRaisesRegex(OutboundRequestBlocked, "non-public or reserved"):
            validate_outbound_url("https://metadata.example.com/", allow_hosts=frozenset())

    @patch("backend.app.core.outbound_http.socket.getaddrinfo")
    def test_exact_allowlist_permits_trusted_internal_http(self, resolver) -> None:
        resolver.return_value = _dns_answer("10.20.30.40")
        target = validate_outbound_url(
            "http://erp.internal.example/data",
            allow_hosts=frozenset({"erp.internal.example"}),
        )
        self.assertTrue(target.is_allowlisted)
        self.assertEqual(target.resolved_addresses, ("10.20.30.40",))

    def test_wildcard_and_cidr_allowlist_entries_are_rejected(self) -> None:
        for value in ("*.example.com", ".example.com", "10.0.0.0/8", "https://example.com"):
            with self.subTest(value=value):
                with self.assertRaises(OutboundRequestBlocked):
                    load_exact_host_allowlist(value)

    def test_embedded_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(OutboundRequestBlocked, "embedded credentials"):
            validate_outbound_url("https://user:secret@example.com/", allow_hosts=frozenset())

    @patch("backend.app.core.outbound_http._send_to_validated_address")
    @patch("backend.app.core.outbound_http.validate_outbound_url")
    def test_redirect_response_is_rejected(self, validator, sender) -> None:
        validator.return_value = ValidatedOutboundTarget(
            url="https://api.example.com/start",
            hostname="api.example.com",
            port=443,
            is_allowlisted=False,
            resolved_addresses=("93.184.216.34",),
        )
        response = requests.Response()
        response.status_code = 302
        response.raw = Mock()
        sender.return_value = response
        with self.assertRaisesRegex(OutboundRequestBlocked, "redirects are disabled"):
            safe_outbound_request("GET", "https://api.example.com/start")

    @patch("backend.app.core.outbound_http._send_to_validated_address")
    @patch("backend.app.core.outbound_http.validate_outbound_url")
    def test_tls_verification_can_only_be_disabled_for_allowlisted_host(self, validator, sender) -> None:
        validator.return_value = ValidatedOutboundTarget(
            url="https://api.example.com/",
            hostname="api.example.com",
            port=443,
            is_allowlisted=False,
            resolved_addresses=("93.184.216.34",),
        )
        with self.assertRaisesRegex(OutboundRequestBlocked, "TLS verification"):
            safe_outbound_request("GET", "https://api.example.com/", verify=False)
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
