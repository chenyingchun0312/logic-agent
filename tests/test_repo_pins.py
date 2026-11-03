"""Guards the third-party pin rules that bootstrap and the release depend on."""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPS = json.loads((REPO / "deps.json").read_text(encoding="utf-8"))
SHA256 = re.compile(r"[0-9a-f]{64}")


def test_downloads_are_pinned_by_hash():
    for name in ("firmware", "python_embed"):
        entry = DEPS[name]
        assert entry["url"].startswith("https://"), name
        assert SHA256.fullmatch(entry["sha256"]), name


def test_sigrok_cli_is_a_repackaged_pin_not_an_upstream_url():
    # Upstream only ships NSIS installers and the nightly build keeps a constant
    # version string, so the upstream URL is provenance, not a pin.
    sigrok = DEPS["sigrok_cli"]
    upstream = sigrok["upstream"]["url"]
    assert upstream.startswith("https://sigrok.org/"), upstream
    assert "installer" in upstream, upstream
    if sigrok["url"]:
        assert sigrok["url"].endswith(".zip"), sigrok["url"]
        assert SHA256.fullmatch(sigrok["sha256"]), sigrok["sha256"]


def test_bootstrap_never_falls_back_to_a_local_sigrok_cli():
    text = (REPO / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8")
    for token in ("Find-SigrokRoot", "-SigrokRoot", "SIGROK_CLI_ROOT"):
        assert token not in text, token
    assert "sigrok_cli.url" in text


def test_common_has_no_local_sigrok_search():
    text = (REPO / "scripts" / "lib" / "common.ps1").read_text(encoding="utf-8")
    for token in ("Find-SigrokRoot", "SIGROK_CLI_ROOT"):
        assert token not in text, token
    assert "Expand-NsisInstaller" in text
