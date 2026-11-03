"""Checks for the npm shell package (packaging/npm) and its launcher.

No Node required for the manifest/regex checks; the two launcher behaviour tests
skip when node is not on PATH. They run the launcher against this source tree
(PYTHONPATH) instead of a built bundle, so they also cover the "no bundled
Python" fallback path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from logic_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
NPM_DIR = REPO_ROOT / "packaging" / "npm"
TEMPLATE = NPM_DIR / "package.json.in"
LAUNCHER = NPM_DIR / "launcher" / "logic-agent-mcp.mjs"
NODE = shutil.which("node")


def _manifest(name: str = "logic-agent-mcp", version: str = __version__) -> dict:
    text = TEMPLATE.read_text(encoding="utf-8")
    return json.loads(text.replace("@@NAME@@", name).replace("@@VERSION@@", version))


def test_template_renders_valid_manifest():
    manifest = _manifest("some-name", "9.9.9")
    assert manifest["name"] == "some-name"
    assert manifest["version"] == "9.9.9"
    # pack-npm.ps1 refuses to publish while a placeholder survives; after the
    # substitutions the rendered manifest must be free of them.
    assert "@@" not in json.dumps(manifest)


def test_manifest_has_no_lifecycle_scripts():
    # Installing must be a pure unpack: no download, no build, no probing.
    manifest = _manifest()
    assert "scripts" not in manifest
    forbidden = {"preinstall", "install", "postinstall", "prepublish", "prepare"}
    assert forbidden.isdisjoint(manifest.get("scripts", {}))


def test_manifest_points_at_the_launcher_and_ships_the_runtime():
    manifest = _manifest()
    assert manifest["type"] == "module"
    assert manifest["bin"] == {"logic-agent-mcp": "launcher/logic-agent-mcp.mjs"}
    files = manifest["files"]
    for required in ("launcher/", "runtime/", "firmware/", "THIRD-PARTY-NOTICES.md"):
        assert required in files


def test_manifest_is_windows_only():
    manifest = _manifest()
    # Publish only what the payload supports; npm then fails fast with
    # EBADPLATFORM instead of installing a package that cannot start.
    assert manifest["os"] == ["win32"]
    assert manifest["cpu"] == ["x64"]
    assert manifest["engines"]["node"]


def test_manifest_license_admits_the_gpl_payload():
    manifest = _manifest()
    assert manifest["license"] == "MIT AND GPL-3.0-or-later"
    assert manifest.get("publishConfig", {}).get("access") == "public"


def test_launcher_never_writes_to_stdout():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/env node")
    # stdout carries JSON-RPC frames; anything else corrupts the stream.
    assert "process.stdout.write" not in source
    assert "console.log" not in source
    assert "stdio: 'inherit'" in source.replace('"', "'")


def _run_launcher(tmp_path: Path, *args: str, stdin: str | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("LOGIC_AGENT_DATA_DIR", None)  # we always pass --data-dir explicitly
    return subprocess.run(
        [NODE, str(LAUNCHER), "--data-dir", str(tmp_path / "data"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=tmp_path,
        env=env,
    )


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_launcher_prints_only_the_version_on_stdout(tmp_path):
    result = _run_launcher(tmp_path, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == __version__


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_launcher_forwards_mcp_jsonrpc(tmp_path):
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )
    result = _run_launcher(tmp_path, stdin=request + "\n")
    assert result.returncode == 0, result.stderr
    frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert frames, f"no JSON-RPC frame on stdout; stderr={result.stderr!r}"
    assert frames[0]["jsonrpc"] == "2.0"
    assert frames[0]["id"] == 1
    assert "result" in frames[0]
