# AGENTS.md

Instructions for AI coding agents (Codex, Claude Code, Kiro, Cursor, ...) and humans working in this repo.

## What this is

An MCP server that lets AI agents drive a logic analyzer (DSLogic Plus first) through a small,
bounded tool set. Raw samples never enter the model context.

```
logic_agent/        Python, stdlib only. MCP server, CLI, archive, analysis, decoder orchestration. MIT.
dslab/              C CLI over DreamSourceLab libsigrok4DSL. JSON on stdout. GPL-3.0-or-later.
decoders_ext/       libsigrokdecode decoders adapted from DSView. GPL-2.0-or-later.
patches/            Our changes to DSView sources, applied by bootstrap.
packaging/          Files that only exist in the portable bundle (launchers, bundle docs, scripts).
packaging/npm/      The npm shell package: Node launcher, package.json template, npm README.
.github/workflows/  ci.yml (tests, dslab build), npm-publish.yml (tag -> npm publish), sigrok-runtime.yml (mint the pinned sigrok-cli zip).
scripts/            bootstrap / build / test / release / pack-npm / pack-sigrok-cli. PowerShell 7.
tests/              pytest. Runs without hardware.
deps.json           Every pinned third-party input (URL + SHA-256). Single source of truth.
```

## Setup and commands (Windows, PowerShell 7)

```powershell
pwsh scripts/bootstrap.ps1          # once; fetches into .deps/ and creates .venv
pwsh scripts/build.ps1              # dslab/build/dslab.exe + DLLs
pwsh scripts/test.ps1               # pytest; add -RequireDecoders to forbid skips
pwsh scripts/release.ps1            # dist/<name>.zip with all non-hardware gates
pwsh scripts/pack-npm.ps1 -SelfTest # dist/npm/<name>-<version>.tgz for `npm publish`
```

Python-only changes need just `.venv` and `scripts/test.ps1`; no C build.

## Rules

- **Do not commit** anything under `.deps/`, `.cache/`, `build/`, `dist/`, `.venv/`, or any `*.fw`/`*.bin`/`*.exe`/`*.dll`.
- **Do not edit `.deps/dsview`** directly. To change DSView code: edit there, then regenerate
  `patches/dsview-v1.3.2-dslab.patch` with `git -C .deps/dsview diff > patches/dsview-v1.3.2-dslab.patch`,
  and document why in `docs/upstream-sync.md`.
- **New third-party input** goes in `deps.json` with a SHA-256, never as a hardcoded path or URL in a script.
- **No machine-specific paths** (`C:\Users\...`, `D:\...`) in code, scripts or docs. Use repo-relative paths,
  `$env:ZEPHYR_ENV_HOME`, or parameters.
- `logic_agent` stays **stdlib-only** at runtime (it ships in an embeddable Python with no site-packages).
  Dev-only deps go in `[project.optional-dependencies].dev`.
- `logic_agent` must not import or link GPL code; it talks to `dslab.exe` / `sigrok-cli.exe` only via subprocess.
  Keep that boundary so the Python package stays MIT.
- Tool results must stay bounded: no raw sample arrays, respect the size limits in `models.py`.
- **The npm package is a shell over the released bundle.** `packaging/npm/launcher/*.mjs` must never
  write to stdout (that is the JSON-RPC channel), and `package.json` must never gain `preinstall` /
  `install` / `postinstall` scripts: installing must not download or build anything. The npm version
  comes only from `logic_agent/__init__.py`; tags are `v<version>`.
- **Firmware writes are dangerous.** Never call `dslab firmware install` from tests or scripts; only print the command.
- **sigrok-cli never comes from the developer machine.** `bootstrap.ps1` downloads the zip pinned by
  `deps.json` `sigrok_cli.url`/`sha256` and nothing else; there is no `-SigrokRoot` fallback. Re-mint the
  pin with the `sigrok runtime` workflow (upstream only ships NSIS installers, and its nightly build keeps
  a constant version string, so the upstream URL is not a pin on its own).
- Hardware is a single exclusive USB device: never run hardware checks in parallel.

## Adding a protocol decoder

1. Register it in `logic_agent/decoders.py` (channel roles, options with types/ranges, `verification`).
2. Add a synthetic-waveform test in `tests/test_decoders.py`; only then mark it `synthetic-waveform`.
3. If the decoder comes from DSView, adapt it under `decoders_ext/<id>/` with a `PORTING.md`.
4. Update `docs/protocol-decoding.md`.

## Adding an MCP client

1. Add a `ClientSpec` in `logic_agent/mcp_clients.py`: config path template, container key
   (`mcpServers` / `servers` / TOML table), detection paths, and any extra fields that client needs.
2. Add a test in `tests/test_mcp_clients.py` that installs and removes the entry in a throwaway
   home. Never read or write the real user config from a test.
3. Document the path in `docs/mcp-clients.md`.

## Adding a backend (e.g. Saleae)

Implement the protocol in `logic_agent/backends/base.py`, return `CapturedSignal`, register it in
`service.py`, add tests with a fake executable like `tests/test_sigrok.py`.

## Definition of done

- `pwsh scripts/test.ps1 -RequireDecoders` passes.
- If C or packaging changed: `pwsh scripts/build.ps1` and `pwsh scripts/release.ps1` pass.
- If npm packaging changed: `pwsh scripts/pack-npm.ps1 -SelfTest` passes (`-SkipVerify` is enough
  when no bundle is built; CI runs it against the real bundle).
- Docs updated where behaviour changed; `CHANGELOG.md` has an entry under *Unreleased*.
- Hardware-affecting changes: say in the PR whether `CHECK.cmd` / `SELF-TEST.cmd` were run on a real device.
