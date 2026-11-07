"""Reproduction for docs/audits/2026-11-07-local-and-supply-chain.md, finding F1.

THREAT_MODEL.md: the watcher "refuses plain http to anything but this computer". The
check reads the host with urlsplit, but requests (urllib3) treats a backslash as the
start of the path, so "http://other-host\\@127.0.0.1:8000" passes the check and then
connects to other-host, sending the password in clear text.

Fixed: the attack tests now pass and stay as regression tests.
"""

import pytest

from decklens.watcher import DeckLensClient

from test_watcher import FakeSession


@pytest.mark.parametrize("url", ["http://evil.example\\@127.0.0.1:8000",
                                 "http://127.0.0.2:8000\\@localhost:8000"])
def test_backslash_tricks_cannot_pass_for_this_computer(url):
    with pytest.raises(ValueError):
        DeckLensClient(url, session=FakeSession())


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000",
                                 "http://[::1]:8000", "https://decklens.example"])
def test_this_computer_and_https_servers_are_still_accepted(url):
    DeckLensClient(url, session=FakeSession())
