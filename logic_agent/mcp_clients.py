"""Register the logic-analyzer MCP server with the local AI agents.

One command writes the right config file for every agent it can find: Codex CLI,
Claude Code, Claude Desktop, GitHub Copilot (VS Code and the Copilot CLI), Cursor,
Kiro, Windsurf and Cline. The launch command is the bundled Python interpreter
running ``-m logic_agent.mcp_server``, so no shell, ``cmd.exe`` or environment
variables are involved: a client that spawns argv directly starts the server.

Design rules:

* Config files are merged, never replaced. Unrelated servers and keys survive.
* A file that cannot be parsed is reported and left untouched; nothing is
  written over a config we do not understand.
* The installed server can be verified end to end (initialize + capabilities)
  before the caller ever opens the agent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .errors import ConfigError

# JSON clients use a hyphenated name (it becomes a tool prefix such as
# mcp__logic-analyzer__capture); Codex tables stay underscored (bare TOML key).
SERVER_NAME = "logic-analyzer"
CODEX_SERVER_NAME = "logic_analyzer"
SCOPES = ("user", "project")

# DeepSeek Harness stores MCP servers as a Cordis YAML patch layer. We own one
# delimited entry so repeat runs replace it and --remove deletes exactly that.
DSH_SERVER_NAME = "logic"
DSH_ITEM_ID = "mcp-logic-agent"
DSH_MARK_START = "# >>> logic-agent (managed by INSTALL-MCP.cmd) >>>"
DSH_MARK_END = "# <<< logic-agent <<<"
DSH_MANAGED = re.compile(
    r"# >>> logic-agent[^\n]*\n.*?# <<< logic-agent[^\n]*\n?",
    re.DOTALL,
)


# --------------------------------------------------------------------------- locations


@dataclass(frozen=True)
class Locations:
    """Where each client keeps its config, and what counts as installed."""

    home: Path
    appdata: Path
    copilot: Path
    cwd: Path
    binaries: bool = True

    @classmethod
    def detect(
        cls,
        home: str | Path | None = None,
        cwd: str | Path | None = None,
        *,
        binaries: bool = True,
    ) -> "Locations":
        real_home = Path.home().resolve()
        chosen = Path(home).expanduser().resolve() if home else real_home
        overridden = chosen != real_home
        if os.name == "nt":
            default_appdata = chosen / "AppData" / "Roaming"
            appdata = default_appdata if overridden else Path(os.environ.get("APPDATA") or default_appdata)
        elif sys.platform == "darwin":
            appdata = chosen / "Library" / "Application Support"
        else:
            default_appdata = chosen / ".config"
            appdata = default_appdata if overridden else Path(os.environ.get("XDG_CONFIG_HOME") or default_appdata)
        copilot = chosen / ".copilot" if overridden else Path(os.environ.get("COPILOT_HOME") or chosen / ".copilot")
        return cls(
            home=chosen,
            appdata=appdata.resolve(),
            copilot=copilot.resolve(),
            cwd=(Path(cwd).expanduser().resolve() if cwd else Path.cwd()),
            binaries=binaries,
        )


def _expand(template: str, loc: Locations) -> Path:
    text = template.format(
        home=loc.home.as_posix(),
        appdata=loc.appdata.as_posix(),
        copilot=loc.copilot.as_posix(),
        cwd=loc.cwd.as_posix(),
    )
    path = Path(text)
    return path if path.is_absolute() else loc.cwd / path


# --------------------------------------------------------------------------- clients


@dataclass(frozen=True)
class ClientSpec:
    """One MCP-capable agent and the file(s) it reads."""

    id: str
    label: str
    fmt: str  # "json" | "toml" | "yaml"
    container: str  # JSON key or TOML table that holds the servers
    user: str  # user-scope config path template
    project: str | None  # project-scope config path template (None: user only)
    detect: tuple[str, ...]  # paths whose existence proves the client is installed
    binary: str | None  # CLI name that proves the client is installed
    stype: str | None  # "type" field some clients require ("stdio" for VS Code)
    extra: dict[str, Any]  # extra entry fields that client expects
    hint: str  # how the user restarts/verifies it


CLIENTS: tuple[ClientSpec, ...] = (
    ClientSpec(
        id="codex",
        label="Codex CLI",
        fmt="toml",
        container="mcp_servers",
        user="{home}/.codex/config.toml",
        project=None,
        detect=("{home}/.codex",),
        binary="codex",
        stype=None,
        extra={},
        hint="restart Codex, then /mcp (or `codex mcp list`)",
    ),
    ClientSpec(
        id="deepseek-harness",
        label="DeepSeek Harness",
        fmt="yaml",
        container="insert",
        user="{home}/.dsh/profiles/desktop/cordis.patch.yml",
        project=None,
        detect=("{home}/.dsh/profiles/desktop",),
        binary=None,
        stype=None,
        extra={},
        hint="restart DeepSeek Harness; the tools show up as mcp__logic__*",
    ),
    ClientSpec(
        id="claude-code",
        label="Claude Code",
        fmt="json",
        container="mcpServers",
        user="{home}/.claude.json",
        project=".mcp.json",
        detect=("{home}/.claude.json", "{home}/.claude"),
        binary="claude",
        stype=None,
        extra={},
        hint="restart Claude Code, then `claude mcp list`",
    ),
    ClientSpec(
        id="claude-desktop",
        label="Claude Desktop",
        fmt="json",
        container="mcpServers",
        user="{appdata}/Claude/claude_desktop_config.json",
        project=None,
        detect=("{appdata}/Claude",),
        binary=None,
        stype=None,
        extra={},
        hint="quit Claude Desktop completely (tray icon) and reopen it",
    ),
    ClientSpec(
        id="copilot-cli",
        label="GitHub Copilot CLI",
        fmt="json",
        container="mcpServers",
        user="{copilot}/mcp-config.json",
        project=".mcp.json",
        detect=("{copilot}",),
        binary="copilot",
        stype="local",
        extra={"tools": ["*"]},
        hint="start `copilot`, then /mcp",
    ),
    ClientSpec(
        id="vscode",
        label="VS Code Copilot",
        fmt="json",
        container="servers",
        user="{appdata}/Code/User/mcp.json",
        project=".vscode/mcp.json",
        detect=("{appdata}/Code/User",),
        binary="code",
        stype="stdio",
        extra={},
        hint="run the command 'MCP: List Servers' and start logic-analyzer",
    ),
    ClientSpec(
        id="cursor",
        label="Cursor",
        fmt="json",
        container="mcpServers",
        user="{home}/.cursor/mcp.json",
        project=".cursor/mcp.json",
        detect=("{home}/.cursor",),
        binary="cursor",
        stype=None,
        extra={},
        hint="Cursor Settings > MCP: the server should be listed and green",
    ),
    ClientSpec(
        id="kiro",
        label="Kiro",
        fmt="json",
        container="mcpServers",
        user="{home}/.kiro/settings/mcp.json",
        project=None,
        detect=("{home}/.kiro",),
        binary="kiro",
        stype=None,
        extra={},
        hint="open the Kiro MCP panel and reconnect logic-analyzer",
    ),
    ClientSpec(
        id="windsurf",
        label="Windsurf",
        fmt="json",
        container="mcpServers",
        user="{home}/.codeium/windsurf/mcp_config.json",
        project=None,
        detect=("{home}/.codeium/windsurf",),
        binary="windsurf",
        stype=None,
        extra={},
        hint="Windsurf > MCP: refresh the server list",
    ),
    ClientSpec(
        id="cline",
        label="Cline (VS Code)",
        fmt="json",
        container="mcpServers",
        user="{appdata}/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
        project=None,
        detect=("{appdata}/Code/User/globalStorage/saoudrizwan.claude-dev",),
        binary=None,
        stype=None,
        extra={"disabled": False, "autoApprove": []},
        hint="Cline > MCP Servers: restart the server",
    ),
)

CLIENT_IDS: tuple[str, ...] = tuple(client.id for client in CLIENTS)


def client_by_id(client_id: str) -> ClientSpec:
    for spec in CLIENTS:
        if spec.id == client_id:
            return spec
    raise ConfigError(
        f"unknown client: {client_id}",
        details={"known_clients": list(CLIENT_IDS)},
    )


# --------------------------------------------------------------------------- launch spec


@dataclass(frozen=True)
class LaunchSpec:
    """Exact argv a client should spawn, plus the environment it needs."""

    command: str
    args: list[str]
    env: dict[str, str]
    mode: str  # "bundle" | "source"
    data_dir: str
    root: str | None = None

    @property
    def command_line(self) -> str:
        return " ".join([_quote(self.command), *(_quote(item) for item in self.args)])

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "mode": self.mode,
            "data_dir": self.data_dir,
            "root": self.root,
        }


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def bundle_root() -> Path | None:
    """Return the portable bundle root, or None when running from a checkout."""
    package = Path(__file__).resolve().parent  # <root>/runtime/app/logic_agent
    if package.parent.name != "app":
        return None
    runtime = package.parent.parent
    if not (runtime / "python").is_dir():
        return None
    return runtime.parent


def _python_in(directory: Path) -> Path | None:
    for name in ("python.exe", "python3", "python"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def build_launch_spec(
    *,
    data_dir: str | Path | None = None,
    backend: str | None = None,
    root: str | Path | None = None,
    home: str | Path | None = None,
) -> LaunchSpec:
    """Build the argv clients should use. Never depends on the current directory."""
    portable = Path(root).expanduser().resolve() if root else bundle_root()
    python: Path | None = None
    environment = {"PYTHONDONTWRITEBYTECODE": "1"}
    if portable is not None:
        python = _python_in(portable / "runtime" / "python")
        chosen_data = Path(data_dir).expanduser() if data_dir else portable / "data" / "captures"
        mode = "bundle"
    else:
        portable = None
        base_home = Path(home).expanduser() if home else Path.home()
        chosen_data = Path(data_dir).expanduser() if data_dir else base_home / ".logic_agent_data"
        mode = "source"
        # Clients start the server from an arbitrary directory; make the package
        # importable without relying on the caller's cwd or an editable install.
        package_root = Path(__file__).resolve().parent.parent
        if package_root.name not in {"site-packages", "dist-packages"}:
            environment["PYTHONPATH"] = str(package_root)
    executable = python or Path(sys.executable)
    resolved_data = chosen_data.resolve()
    args = ["-m", "logic_agent.mcp_server", "--data-dir", str(resolved_data)]
    if backend:
        args += ["--backend", backend]
    return LaunchSpec(
        command=str(executable),
        args=args,
        env=environment,
        mode=mode,
        data_dir=str(resolved_data),
        root=str(portable) if portable is not None else None,
    )


# --------------------------------------------------------------------------- config I/O


def server_entry(spec: ClientSpec, launch: LaunchSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if spec.stype:
        entry["type"] = spec.stype
    entry["command"] = launch.command
    entry["args"] = list(launch.args)
    if launch.env:
        entry["env"] = dict(launch.env)
    for key, value in spec.extra.items():
        entry.setdefault(key, value)
    return entry


def toml_block(spec: ClientSpec, launch: LaunchSpec) -> str:
    lines = [
        f"[{spec.container}.{CODEX_SERVER_NAME}]",
        f"command = {_toml_string(launch.command)}",
        "args = [" + ", ".join(_toml_string(item) for item in launch.args) + "]",
    ]
    if launch.env:
        pairs = ", ".join(f"{key} = {_toml_string(value)}" for key, value in launch.env.items())
        lines.append(f"env = {{ {pairs} }}")
    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} is not UTF-8: {exc}") from exc


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = _read_text(path)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc}); left unchanged") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a JSON object; left unchanged")
    return data


def install_json(path: Path, spec: ClientSpec, launch: LaunchSpec, *, remove: bool = False) -> str:
    data = _load_json(path)
    before = json.dumps(data, indent=2, ensure_ascii=False)
    servers = data.get(spec.container)
    if servers is None:
        if remove:
            return "absent"
        servers = {}
    if not isinstance(servers, dict):
        raise ConfigError(f'"{spec.container}" in {path} is not a JSON object; left unchanged')
    if remove:
        if SERVER_NAME not in servers:
            return "absent"
        del servers[SERVER_NAME]
        if servers:
            data[spec.container] = servers
        else:
            data.pop(spec.container, None)
        # Removing the last entry should undo the install, not leave a "{}" file.
        if not data:
            path.unlink(missing_ok=True)
            return "written"
    else:
        servers[SERVER_NAME] = server_entry(spec, launch)
        data[spec.container] = servers
    after = json.dumps(data, indent=2, ensure_ascii=False)
    if after == before:
        return "unchanged"
    _atomic_write(path, after + "\n")
    return "written"


def _is_codex_header(line: str, table: str, name: str) -> bool:
    body = line.strip().split("#", 1)[0].strip()
    if not body.startswith("[") or body.startswith("[[") or not body.endswith("]"):
        return False
    inner = body[1:-1].strip()
    for candidate in (f"{table}.{name}", f'{table}."{name}"', f"{table}.'{name}'"):
        if inner == candidate or inner.startswith(candidate + "."):
            return True
    return False


def strip_toml_table(text: str, table: str, name: str) -> tuple[str, bool]:
    """Remove ``[table.name]`` and its sub-tables; keep everything else verbatim."""
    kept: list[str] = []
    skipping = False
    removed = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            skipping = _is_codex_header(line, table, name)
            removed = removed or skipping
        if not skipping:
            kept.append(line)
    result = "".join(kept)
    if removed:
        result = result.rstrip("\n")
        result = re.sub(r"\n{3,}", "\n\n", result)
        result = result + "\n" if result else ""
    return result, removed


def install_toml(path: Path, spec: ClientSpec, launch: LaunchSpec, *, remove: bool = False) -> str:
    text = _read_text(path) if path.exists() else ""
    remaining, found = strip_toml_table(text, spec.container, CODEX_SERVER_NAME)
    if remove:
        if not found:
            return "absent"
        result = remaining
    else:
        block = toml_block(spec, launch)
        result = (remaining.rstrip("\n") + "\n\n" + block) if remaining.strip() else block
    try:
        parsed = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} cannot be parsed as TOML ({exc}); left unchanged") from exc
    if remove:
        # Removing the last table should undo the install, not leave an empty file.
        if not result.strip():
            path.unlink(missing_ok=True)
            return "written"
        _atomic_write(path, result)
        return "written"
    table = parsed.get(spec.container)
    entry = table.get(CODEX_SERVER_NAME) if isinstance(table, dict) else None
    if not isinstance(entry, dict) or entry.get("command") != launch.command:
        raise ConfigError(f"could not add [{spec.container}.{CODEX_SERVER_NAME}] to {path}; left unchanged")
    if result == text:
        return "unchanged"
    _atomic_write(path, result)
    return "written"


def _yaml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dsh_item(launch: LaunchSpec) -> str:
    """The Cordis patch entry that gives DeepSeek Harness the logic analyzer."""
    cwd = launch.root or str(Path(__file__).resolve().parent.parent)
    lines = [
        "- insert:",
        f"    - id: {DSH_ITEM_ID}",
        '      name: "@deepseek-ai/dsh-mcp-client"',
        "      config:",
        f"        serverName: {DSH_SERVER_NAME}",
        "        transport: stdio",
        f"        command: {_yaml_string(launch.command)}",
        "        args:",
        *(f"          - {_yaml_string(item)}" for item in launch.args),
        f"        cwd: {_yaml_string(cwd)}",
        "        toolCallTimeoutMs: 120000",
        "        failOnStartupError: false",
    ]
    return "\n".join(lines) + "\n"


def dsh_block(launch: LaunchSpec) -> str:
    return f"{DSH_MARK_START}\n{dsh_item(launch)}{DSH_MARK_END}\n"


def _strip_managed_yaml(text: str) -> tuple[str, bool]:
    if DSH_MARK_START not in text:
        return text, False
    stripped = DSH_MANAGED.sub("", text)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).rstrip("\n")
    return (stripped + "\n") if stripped else "", True


def _is_yaml_sequence(text: str) -> bool:
    """Accept empty files, ``[]`` and documents whose first token is a sequence item."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped.startswith("- ") or stripped == "-" or stripped.startswith("[")
    return True


def _yaml_items(text: str) -> list[tuple[int, int]]:
    """(start, end) line ranges of the top-level ``- ...`` entries."""
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("- ")]
    spans: list[tuple[int, int]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        spans.append((start, end))
    return spans


def _find_dsh_item(text: str) -> tuple[int, int] | None:
    """Locate a hand-written logic-analyzer entry so we can adopt it instead of duplicating."""
    lines = text.splitlines(keepends=True)
    for start, end in _yaml_items(text):
        chunk = "".join(lines[start:end])
        if "@deepseek-ai/dsh-mcp-client" not in chunk:
            continue
        if f"id: {DSH_ITEM_ID}" in chunk or f"serverName: {DSH_SERVER_NAME}" in chunk:
            return start, end
    return None


def _backup(path: Path, text: str) -> None:
    if not text.strip():
        return
    _atomic_write(path.with_name(path.name + ".bak"), text)


def install_yaml(path: Path, spec: ClientSpec, launch: LaunchSpec, *, remove: bool = False) -> str:
    text = _read_text(path) if path.exists() else ""
    remaining, had_block = _strip_managed_yaml(text)
    if remove:
        if not had_block:
            return "absent"
        _atomic_write(path, remaining if remaining.strip() else "[]\n")
        return "written"

    if not _is_yaml_sequence(remaining):
        raise ConfigError(f"{path} 的顶层不是 YAML 列表，拒绝写入（未修改）")

    block = dsh_block(launch)
    if had_block:
        result = (remaining.rstrip("\n") + "\n\n" + block) if remaining.strip() else block
    else:
        span = _find_dsh_item(text)
        if span is not None:
            # Adopt the hand-written entry: same server, but now owned by the installer.
            start, end = span
            lines = text.splitlines(keepends=True)
            suffix = "".join(lines[end:])
            result = "".join(lines[:start]) + block + ("\n" + suffix if suffix.strip() else "")
            _backup(path, text)
        elif text.strip():
            result = text.rstrip("\n") + "\n\n" + block
            _backup(path, text)
        else:
            result = block

    if result == text:
        return "unchanged"
    if not _is_yaml_sequence(result):
        raise ConfigError(f"{path}: 写入后不再是 YAML 列表，已放弃（未修改）")
    _atomic_write(path, result)
    return "written"


def apply_client(
    spec: ClientSpec,
    launch: LaunchSpec,
    path: Path,
    *,
    remove: bool = False,
    dry_run: bool = False,
) -> tuple[str, str]:
    if dry_run:
        return "dry-run", snippet(spec, launch, path)
    if spec.fmt == "toml":
        status = install_toml(path, spec, launch, remove=remove)
    elif spec.fmt == "yaml":
        status = install_yaml(path, spec, launch, remove=remove)
    else:
        status = install_json(path, spec, launch, remove=remove)
    if remove and status == "written":
        status = "removed"
    return status, ""


def snippet(spec: ClientSpec, launch: LaunchSpec, path: Path) -> str:
    if spec.fmt == "toml":
        return f"# {path}\n" + toml_block(spec, launch)
    if spec.fmt == "yaml":
        return f"# {path}\n" + dsh_block(launch)
    document = {spec.container: {SERVER_NAME: server_entry(spec, launch)}}
    return f"// {path}\n" + json.dumps(document, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- reporting


@dataclass
class Result:
    id: str
    label: str
    status: str
    path: str | None = None
    detail: str = ""
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "path": self.path,
            "detail": self.detail,
            "hint": self.hint,
        }


@dataclass
class Installation:
    launch: LaunchSpec
    results: list[Result] = field(default_factory=list)
    detected: list[str] = field(default_factory=list)
    not_detected: list[str] = field(default_factory=list)
    verification: dict[str, Any] | None = None
    removed: bool = False
    include_all: bool = False
    explicit: bool = False

    @property
    def ok(self) -> bool:
        if any(item.status == "error" for item in self.results):
            return False
        if self.verification is not None and not self.verification.get("ok"):
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "launch": self.launch.as_dict(),
            "removed": self.removed,
            "clients": [item.as_dict() for item in self.results],
            "detected": self.detected,
            "not_detected": self.not_detected,
            "verification": self.verification,
        }


def detect_clients(loc: Locations) -> list[ClientSpec]:
    return [spec for spec in CLIENTS if _is_detected(spec, loc)]


def _is_detected(spec: ClientSpec, loc: Locations) -> bool:
    if any(_expand(template, loc).exists() for template in spec.detect):
        return True
    if spec.binary and loc.binaries and shutil.which(spec.binary):
        return True
    return False


def list_clients(loc: Locations) -> list[dict[str, Any]]:
    """Describe every supported client: detected or not, and exactly where its config lives."""
    rows: list[dict[str, Any]] = []
    for spec in CLIENTS:
        rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "detected": _is_detected(spec, loc),
                "format": spec.fmt,
                "user_config": str(_expand(spec.user, loc)),
                "project_config": str(_expand(spec.project, loc)) if spec.project else None,
                "hint": spec.hint,
            }
        )
    return rows


def format_clients(rows: list[dict[str, Any]]) -> str:
    width = max(len(str(row["id"])) for row in rows) + 2
    lines = ["Supported agents ('yes' means it was found on this machine):", ""]
    for row in rows:
        mark = "yes" if row["detected"] else "no "
        lines.append(f"  {row['id']:<{width}}{mark:<4}{row['user_config']}")
    lines.append("")
    lines.append("Add one with `logic-agent mcp-install <id>`, or every detected agent with `logic-agent mcp-install`.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- verification

def verify_launch(launch: LaunchSpec, *, timeout_s: float = 60.0) -> dict[str, Any]:
    """Spawn the server exactly as a client would and require a real handshake."""
    command = Path(launch.command)
    if not (command.is_file() or shutil.which(launch.command)):
        return {"ok": False, "detail": f"command not found: {launch.command}"}
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "logic-agent-setup", "version": __version__},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "capabilities", "arguments": {}}},
    ]
    payload = "\n".join(json.dumps(item) for item in requests) + "\n"
    environment = os.environ.copy()
    environment.update(launch.env)
    try:
        completed = subprocess.run(
            [launch.command, *launch.args],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=environment,
            # A client may start the server from any directory; prove that it works.
            cwd=tempfile.gettempdir(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    if "logic-agent" not in completed.stdout:
        detail = (completed.stderr or completed.stdout or "").strip()[-600:]
        return {"ok": False, "detail": detail or f"exit code {completed.returncode}, no MCP reply"}
    backends: dict[str, Any] = {}
    protocol_count = None
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != 2:
            continue
        structured = (message.get("result") or {}).get("structuredContent") or {}
        backends = {
            name: bool(value.get("available")) for name, value in (structured.get("backends") or {}).items()
        }
        protocols = (structured.get("protocol_decoding") or {}).get("supported_protocols") or []
        protocol_count = len(protocols)
    if not backends:
        return {"ok": False, "detail": "server answered initialize but not capabilities"}
    return {"ok": True, "backends": backends, "protocols": protocol_count}


# --------------------------------------------------------------------------- entry point


def install(
    *,
    locations: Locations | None = None,
    scope: str = "user",
    only: Iterable[str] = (),
    include_all: bool = False,
    remove: bool = False,
    dry_run: bool = False,
    verify: bool = True,
    data_dir: str | Path | None = None,
    backend: str | None = None,
    root: str | Path | None = None,
) -> Installation:
    if scope not in SCOPES:
        raise ConfigError(f"unknown scope: {scope}", details={"scopes": list(SCOPES)})
    loc = locations or Locations.detect()
    requested = [client_by_id(item) for item in only]
    installation = Installation(
        launch=build_launch_spec(data_dir=data_dir, backend=backend, root=root, home=loc.home),
        include_all=include_all,
        explicit=bool(requested),
    )
    installation.detected = [spec.id for spec in detect_clients(loc)]
    installation.not_detected = [spec.id for spec in CLIENTS if spec.id not in installation.detected]
    installation.removed = remove

    for spec in requested or CLIENTS:
        if requested and scope == "project" and spec.project is None:
            installation.results.append(Result(spec.id, spec.label, "skipped", detail="no project-scope config"))
            continue
        if not requested and not include_all and scope == "user" and spec.id not in installation.detected:
            continue
        template = spec.user if scope == "user" else spec.project
        if template is None:
            installation.results.append(Result(spec.id, spec.label, "skipped", detail="no project-scope config"))
            continue
        target = _expand(template, loc)
        try:
            status, detail = apply_client(spec, installation.launch, target, remove=remove, dry_run=dry_run)
        except (OSError, ConfigError) as exc:
            installation.results.append(Result(spec.id, spec.label, "error", str(target), str(exc), spec.hint))
            continue
        installation.results.append(Result(spec.id, spec.label, status, str(target), detail, spec.hint))

    wrote = any(item.status in {"written", "unchanged"} for item in installation.results)
    if verify and not dry_run and not remove and wrote:
        installation.verification = verify_launch(installation.launch)
    return installation


# --------------------------------------------------------------------------- text output


_MARKERS = {
    "written": "added",
    "unchanged": "ok",
    "removed": "removed",
    "absent": "absent",
    "skipped": "skipped",
    "dry-run": "preview",
    "error": "ERROR",
}


def format_report(installation: Installation, *, dry_run: bool = False) -> str:
    launch = installation.launch
    lines = [
        "logic-agent MCP setup",
        f"  mode    : {launch.mode}" + (f" ({launch.root})" if launch.root else ""),
        f"  command : {launch.command_line}",
        "",
    ]
    if dry_run:
        lines.append("Copy-paste preview (nothing was written):")
        for item in installation.results:
            lines.append("")
            lines.append(item.detail)
        lines.append("")
        lines.append("Write them automatically by running this command without --dry-run.")
        return "\n".join(lines)

    if installation.results:
        lines.append("Remove" if installation.removed else "Install")
        width = max(len(item.id) for item in installation.results) + 2
        for item in installation.results:
            marker = _MARKERS.get(item.status, item.status)
            target = item.path or ""
            lines.append(f"  {item.id:<{width}}{marker:<9}{target}")
            if item.detail and item.status == "error":
                for chunk in item.detail.splitlines():
                    lines.append(f"  {'':<{width}}{'':<9}{chunk}")
        lines.append("")
    if installation.verification:
        check = installation.verification
        if check.get("ok"):
            backends = ", ".join(f"{name}={'yes' if up else 'no'}" for name, up in (check.get("backends") or {}).items())
            protocols = check.get("protocols")
            suffix = f"; {protocols} protocols" if protocols else ""
            lines.append(f"Verified: the server starts and answers capabilities ({backends}{suffix}).")
        else:
            lines.append(f"Verification FAILED: {check.get('detail')}")
        lines.append("")
    if installation.removed and installation.results:
        lines.append("Restart the app if it is open: the logic-analyzer entry is gone.")
        lines.append("")
    elif installation.results:
        hints = sorted({item.hint for item in installation.results if item.hint})
        lines.append('Next: restart the app, then ask the agent to call the "capabilities" tool.')
        if len(hints) <= 4:
            for hint in hints:
                lines.append(f"  - {hint}")
        else:
            lines.append("  - quit the client completely (Claude Desktop: use the tray icon) and reopen it")
        lines.append("")
    if installation.not_detected and not installation.include_all and not installation.explicit:
        lines.append("Not detected: " + ", ".join(installation.not_detected))
        lines.append("  `--all` configures them anyway; `--dry-run` prints copy-paste snippets.")
        lines.append("")
    if not installation.results and not installation.not_detected:
        lines.append("No agent was configured.")
    return "\n".join(lines).rstrip() + "\n"
