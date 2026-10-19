"""Mail adapter: which connection security a given account configuration gets."""

from __future__ import annotations

import pytest

from common.tools import ToolError
from internal_tools.mail.adapters import imap_smtp


@pytest.mark.parametrize(
    ("config", "imap", "smtp"),
    [
        ({}, "ssl", "ssl"),                                   # old accounts: unchanged behaviour
        ({"smtp_port": 587}, "ssl", "starttls"),              # outlook-style preset
        ({"security": "none"}, "none", "none"),
        ({"security": "none", "smtp_security": "starttls"}, "none", "starttls"),
    ],
)
def test_connection_security(config, imap, smtp):
    assert imap_smtp.connection_security(config, "imap") == imap
    assert imap_smtp.connection_security(config, "smtp") == smtp


def test_unknown_security_is_an_error():
    with pytest.raises(ToolError):
        imap_smtp.connection_security({"security": "tls13"}, "imap")
