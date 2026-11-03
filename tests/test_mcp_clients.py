"""Tests for the one-command MCP client setup (logic_agent.mcp_clients).

Every test runs against a throwaway home directory: the real user config files are
never read or written.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from logic_agent import mcp_clients
from logic_agent.cli import main
from logic_agent.mcp_clients import (
    CLIENT_IDS,
    CODEX_SERVER_NAME,
    DSH_ITEM_ID,
    DSH_MARK_END,
    DSH_MARK_START,
    SERVER_NAME,
    LaunchSpec,
    Locations,
    build_launch_spec,
    install,
    verify_launch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Shaped like a real DeepSeek Harness profile: other plugins plus a hand-written
# logic-analyzer entry that the installer should adopt rather than duplicate.
DSH_PROFILE = """\
- id: ui-theme
  name: "@deepseek-ai/dsh-client-ui-theme"
  config:
    preference: dark
# DSLogic 逻辑分析仪的 MCP 服务
- insert:
    - id: mcp-logic-agent
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: logic
        transport: stdio
        command: cmd
        args:
          - "/c"
          - "D:\\\\workspace\\\\mcp\\\\logic-agent\\\\MCP.cmd"
        cwd: "D:\\\\workspace\\\\mcp\\\\logic-agent"
        toolCallTimeoutMs: 120000
        failOnStartupError: false
"""


def _locations(tmp_path: Path, *, cwd: Path | None = None) -> Locations:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    project = cwd or (tmp_path / "project")
    project.mkdir(parents=True, exist_ok=True)
    return Locations.detect(home=home, cwd=project, binaries=False)


def _touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_codex_toml_keeps_existing_settings(tmp_path):
    loc = _locations(tmp_path)
    config = _touch(
        loc.home / ".codex" / "config.toml",
        'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other.exe"\n',
    )

    result = install(locations=loc, verify=False)

    assert result.ok
    assert [item.id for item in result.results] == ["codex"]
    assert result.results[0].status == "written"
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["model"] == "gpt-5"
    assert data["mcp_servers"]["other"]["command"] == "other.exe"
    entry = data["mcp_servers"][CODEX_SERVER_NAME]
    assert entry["args"][:2] == ["-m", "logic_agent.mcp_server"]
    assert entry["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert Path(entry["command"]).is_file()


def test_codex_toml_is_idempotent(tmp_path):
    loc = _locations(tmp_path)
    _touch(loc.home / ".codex" / "config.toml", "")

    first = install(locations=loc, verify=False)
    second = install(locations=loc, verify=False)

    assert first.results[0].status == "written"
    assert second.results[0].status == "unchanged"
    text = (loc.home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert text.count(f"[mcp_servers.{CODEX_SERVER_NAME}]") == 1


def test_codex_toml_replaces_a_stale_entry_with_comments(tmp_path):
    loc = _locations(tmp_path)
    config = _touch(
        loc.home / ".codex" / "config.toml",
        f'[mcp_servers.{CODEX_SERVER_NAME}]  # old\ncommand = "old.exe"\n\n[mcp_servers.{CODEX_SERVER_NAME}.env]\nA = "1"\n',
    )

    assert install(locations=loc, verify=False).results[0].status == "written"

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["mcp_servers"][CODEX_SERVER_NAME]["command"] != "old.exe"
    assert "A" not in data["mcp_servers"][CODEX_SERVER_NAME]["env"]


def test_json_client_merges_and_keeps_other_servers(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.home / ".kiro")  # detection marker directory
    settings = _touch(
        loc.home / ".kiro" / "settings" / "mcp.json",
        json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}}, indent=2),
    )

    result = install(locations=loc, verify=False)

    assert result.ok
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["mcpServers"]["github"]["command"] == "gh-mcp"
    entry = data["mcpServers"][SERVER_NAME]
    assert entry["command"] == result.launch.command
    assert entry["args"] == result.launch.args


def test_vscode_uses_servers_object_and_stdio_type(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.appdata / "Code" / "User")
    target = loc.appdata / "Code" / "User" / "mcp.json"

    result = install(locations=loc, verify=False)

    assert [item.id for item in result.results] == ["vscode"]
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "mcpServers" not in data
    assert data["servers"][SERVER_NAME]["type"] == "stdio"


def test_remove_deletes_only_our_entry(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.home / ".copilot")
    settings = _touch(
        loc.copilot / "mcp-config.json",
        json.dumps({"mcpServers": {"keep": {"command": "keep.exe"}}}),
    )

    install(locations=loc, verify=False)
    removed = install(locations=loc, remove=True, verify=False)

    assert removed.ok
    assert removed.results[0].status == "removed"
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"keep": {"command": "keep.exe"}}
    assert install(locations=loc, remove=True, verify=False).results[0].status == "absent"


def test_remove_undoes_the_install_when_it_created_the_file(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.home / ".kiro")

    install(locations=loc, verify=False)
    created = loc.home / ".kiro" / "settings" / "mcp.json"
    assert created.is_file()

    removed = install(locations=loc, remove=True, verify=False)

    assert removed.results[0].status == "removed"
    assert not created.exists(), "an empty config file should not be left behind"


def test_remove_reports_absent_when_nothing_was_installed(tmp_path):
    loc = _locations(tmp_path)
    config = _touch(loc.home / ".codex" / "config.toml", 'model = "keep"\n')

    result = install(locations=loc, remove=True, verify=False)

    assert result.ok
    assert result.results[0].status == "absent"
    assert config.read_text(encoding="utf-8") == 'model = "keep"\n'


def test_copilot_cli_entry_uses_local_type_and_all_tools(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.home / ".copilot")

    install(locations=loc, verify=False)

    data = json.loads((loc.copilot / "mcp-config.json").read_text(encoding="utf-8"))
    entry = data["mcpServers"][SERVER_NAME]
    assert entry["type"] == "local"
    assert entry["tools"] == ["*"]


def test_invalid_json_is_reported_and_not_overwritten(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.home / ".cursor")
    broken = _touch(loc.home / ".cursor" / "mcp.json", '{"mcpServers": {')

    result = install(locations=loc, verify=False)

    assert result.ok is False
    assert result.results[0].status == "error"
    assert "not valid JSON" in result.results[0].detail
    assert broken.read_text(encoding="utf-8") == '{"mcpServers": {'


def test_dry_run_writes_nothing(tmp_path):
    loc = _locations(tmp_path)
    _touch(loc.home / ".codex" / "config.toml", 'model = "keep"\n')
    before = (loc.home / ".codex" / "config.toml").read_text(encoding="utf-8")

    result = install(locations=loc, dry_run=True, verify=False)

    assert result.ok
    assert result.results[0].status == "dry-run"
    assert f"[mcp_servers.{CODEX_SERVER_NAME}]" in result.results[0].detail
    assert (loc.home / ".codex" / "config.toml").read_text(encoding="utf-8") == before


def test_only_detected_clients_are_configured_by_default(tmp_path):
    loc = _locations(tmp_path)
    _mkdir(loc.home / ".kiro")

    assert [item.id for item in install(locations=loc, verify=False).results] == ["kiro"]

    everything = install(locations=loc, include_all=True, verify=False)
    assert [item.id for item in everything.results] == list(CLIENT_IDS)


def test_project_scope_writes_workspace_files(tmp_path):
    loc = _locations(tmp_path)

    result = install(locations=loc, scope="project", only=["claude-code", "vscode"], verify=False)

    assert [item.status for item in result.results] == ["written", "written"]
    shared = json.loads((loc.cwd / ".mcp.json").read_text(encoding="utf-8"))
    assert shared["mcpServers"][SERVER_NAME]["command"] == result.launch.command
    workspace = json.loads((loc.cwd / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert workspace["servers"][SERVER_NAME]["type"] == "stdio"


def test_project_scope_skips_clients_without_project_config(tmp_path):
    loc = _locations(tmp_path)

    result = install(locations=loc, scope="project", only=["codex"], verify=False)

    assert result.results[0].status == "skipped"


def test_unknown_client_is_a_config_error(tmp_path):
    from logic_agent.errors import ConfigError

    loc = _locations(tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        install(locations=loc, only=["nope"], verify=False)
    assert "nope" in str(excinfo.value)


def test_bundle_launch_spec_uses_bundled_python(tmp_path):
    root = tmp_path / "logic-agent"
    (root / "runtime" / "python").mkdir(parents=True)
    (root / "runtime" / "python" / "python.exe").write_bytes(b"MZ")

    spec = build_launch_spec(root=root)

    assert spec.mode == "bundle"
    assert spec.command.endswith("python.exe")
    assert spec.args[0:2] == ["-m", "logic_agent.mcp_server"]
    assert spec.args[3] == str((root / "data" / "captures").resolve())
    assert "PYTHONPATH" not in spec.env


def test_source_launch_spec_pins_data_dir_and_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_clients, "bundle_root", lambda: None)

    spec = build_launch_spec(home=tmp_path / "home")

    assert spec.mode == "source"
    assert spec.args[3] == str((tmp_path / "home" / ".logic_agent_data").resolve())
    assert spec.env["PYTHONPATH"] == str(REPO_ROOT)


def test_verify_launch_handshake(tmp_path):
    spec = LaunchSpec(
        command=sys.executable,
        args=[
            "-m", "logic_agent.mcp_server",
            "--backend", "mock",
            "--data-dir", str(tmp_path / "data"),
        ],
        env={"PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        mode="source",
        data_dir=str(tmp_path / "data"),
    )

    check = verify_launch(spec, timeout_s=120)

    assert check["ok"] is True, check.get("detail")
    assert check["backends"]["mock"] is True
    assert check["protocols"] >= 20


def test_cli_json_output_for_install(tmp_path, capsys):
    home = tmp_path / "home"
    _touch(home / ".codex" / "config.toml", "")

    code = main(["mcp-install", "--home", str(home), "--no-verify", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["clients"][0]["id"] == "codex"
    assert payload["clients"][0]["status"] == "written"
    assert Path(payload["clients"][0]["path"]).is_file()


def test_cli_list_names_every_supported_client(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()

    assert main(["mcp-install", "--home", str(home), "--list", "--json"]) == 0

    rows = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in rows] == list(CLIENT_IDS)
    assert all(row["user_config"].startswith(str(home)) for row in rows)


def test_report_hides_not_detected_footer_when_every_client_was_requested(tmp_path):
    from logic_agent.mcp_clients import format_report

    loc = _locations(tmp_path)
    _mkdir(loc.home / ".kiro")

    detected_only = format_report(install(locations=loc, verify=False))
    assert "Not detected:" in detected_only
    assert "kiro" in detected_only

    everything = format_report(install(locations=loc, include_all=True, verify=False))
    assert "Not detected:" not in everything


def test_cli_dry_run_reports_preview(tmp_path, capsys):
    home = tmp_path / "home"
    _touch(home / ".codex" / "config.toml", "")

    code = main(["mcp-install", "--home", str(home), "--dry-run", "--no-verify"])

    assert code == 0
    output = capsys.readouterr().out
    assert "nothing was written" in output
    assert "Press any key" not in output
    assert f"[mcp_servers.{CODEX_SERVER_NAME}]" in output

def _dsh_profile(loc: Locations) -> Path:
    profile = loc.home / ".dsh" / "profiles" / "desktop"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def test_deepseek_harness_creates_managed_block(tmp_path):
    loc = _locations(tmp_path)
    profile = _dsh_profile(loc)

    result = install(locations=loc, verify=False)

    assert [item.id for item in result.results] == ["deepseek-harness"]
    assert result.results[0].status == "written"
    target = profile / "cordis.patch.yml"
    text = target.read_text(encoding="utf-8")
    assert DSH_MARK_START in text and DSH_MARK_END in text
    assert f"id: {DSH_ITEM_ID}" in text
    assert 'name: "@deepseek-ai/dsh-mcp-client"' in text
    assert "serverName: logic" in text
    assert "transport: stdio" in text
    assert "toolCallTimeoutMs: 120000" in text
    assert "failOnStartupError: false" in text
    assert "-m" in text and "logic_agent.mcp_server" in text
    # YAML double-quoted scalars escape every backslash.
    assert result.launch.command.replace("\\", "\\\\") in text


def test_deepseek_harness_is_idempotent(tmp_path):
    loc = _locations(tmp_path)
    _dsh_profile(loc)

    first = install(locations=loc, verify=False)
    second = install(locations=loc, verify=False)

    assert first.results[0].status == "written"
    assert second.results[0].status == "unchanged"
    text = (loc.home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml").read_text(encoding="utf-8")
    assert text.count(DSH_MARK_START) == 1
    assert text.count("@deepseek-ai/dsh-mcp-client") == 1


def test_deepseek_harness_adopts_hand_written_entry(tmp_path):
    loc = _locations(tmp_path)
    profile = _dsh_profile(loc)
    target = profile / "cordis.patch.yml"
    target.write_text(DSH_PROFILE, encoding="utf-8")

    result = install(locations=loc, verify=False)

    assert result.results[0].status == "written"
    text = target.read_text(encoding="utf-8")
    assert text.count("@deepseek-ai/dsh-mcp-client") == 1
    assert "workspace" not in text, "the stale hand-written path should be replaced"
    assert "- id: ui-theme" in text, "unrelated plugins must survive"
    assert text.count(DSH_MARK_START) == 1
    backup = profile / "cordis.patch.yml.bak"
    assert backup.is_file() and backup.read_text(encoding="utf-8") == DSH_PROFILE


def test_deepseek_harness_remove_restores_an_empty_patch_file(tmp_path):
    loc = _locations(tmp_path)
    profile = _dsh_profile(loc)
    target = profile / "cordis.patch.yml"

    install(locations=loc, verify=False)
    removed = install(locations=loc, remove=True, verify=False)

    assert removed.results[0].status == "removed"
    assert target.read_text(encoding="utf-8").strip() == "[]"
    assert install(locations=loc, remove=True, verify=False).results[0].status == "absent"


def test_deepseek_harness_remove_keeps_other_plugins(tmp_path):
    loc = _locations(tmp_path)
    profile = _dsh_profile(loc)
    target = profile / "cordis.patch.yml"
    target.write_text(DSH_PROFILE, encoding="utf-8")

    install(locations=loc, verify=False)
    install(locations=loc, remove=True, verify=False)

    text = target.read_text(encoding="utf-8")
    assert "@deepseek-ai/dsh-mcp-client" not in text
    assert "- id: ui-theme" in text


def test_deepseek_harness_refuses_a_non_sequence_file(tmp_path):
    loc = _locations(tmp_path)
    profile = _dsh_profile(loc)
    target = profile / "cordis.patch.yml"
    target.write_text("foo: bar\n", encoding="utf-8")

    result = install(locations=loc, verify=False)

    assert result.ok is False
    assert result.results[0].status == "error"
    assert "YAML" in result.results[0].detail
    assert target.read_text(encoding="utf-8") == "foo: bar\n"


def test_deepseek_harness_dry_run_writes_nothing(tmp_path):
    loc = _locations(tmp_path)
    profile = _dsh_profile(loc)

    result = install(locations=loc, dry_run=True, verify=False)

    assert result.results[0].status == "dry-run"
    assert DSH_MARK_START in result.results[0].detail
    assert not (profile / "cordis.patch.yml").exists()


def test_deepseek_harness_has_no_project_scope(tmp_path):
    loc = _locations(tmp_path)

    result = install(locations=loc, scope="project", only=["deepseek-harness"], verify=False)

    assert result.results[0].status == "skipped"
