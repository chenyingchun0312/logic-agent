# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
版本号只在 `logic_agent/__init__.py` 中维护，`pyproject.toml` 和发布脚本都从那里读取。

## [Unreleased]

### Added
- `logic-agent mcp-install`（发布包里的 `INSTALL-MCP.cmd`）：一条命令把 `logic-analyzer` 加进本机的 Codex、Claude Code、Claude Desktop、GitHub Copilot（VS Code / CLI）、Cursor、Kiro、Windsurf 和 Cline。默认只处理检测到的客户端，合并写入（其它 server 原样保留），配置无法解析时报告且不覆盖，写完用真实 `initialize` + `capabilities` 验证启动。支持 `--list`、`--dry-run`、`--remove`、`--scope project`、`--json`、`--backend`、`--data-dir`。
- `docs/mcp-clients.md`（发布包内 `docs\MCP-CLIENTS.md`）：客户端配置位置、手动 JSON/TOML、常见排错。
- `CHECK.cmd` 增加 `mcp-install` 门禁：在临时 home 里写入配置，确认启动命令是包内解释器、不经过 `cmd.exe`、也不依赖 `DSLAB_*` 环境变量。

### Changed
- MCP 配置改为直接 spawn 包内 `runtime\python\python.exe -m logic_agent.mcp_server`，不再经过 `cmd /c MCP.cmd`：空格路径和批处理引号问题消失，各客户端配置形式统一。`MCP.cmd` 保留且行为不变。
- `DslabBackend` 在便携布局下自动发现 `runtime/bin/dslab.exe`，所以客户端配置里不需要 `DSLAB_EXE`/`DSLAB_RES_DIR`。

## [1.1.1] - 2026-10-31

仅打包变化，`logic_agent` 和 `dslab` 代码与 1.1.0 相同。

### Changed
- `release.ps1 -NoDsviewDriver`：公开 Release 不带 DreamSourceLab 驱动包，只带 Zadig；`INSTALL-DRIVER.cmd` 找不到驱动包时自动改用 Zadig。
- 公开发布包的 `VERSION.txt` 会写明随包带了哪种驱动。

## [1.1.0] - 2026-10-31

### Changed
- 构建与发布改为可复现：`scripts/bootstrap.ps1` 按 `deps.json` 固定版本和 SHA-256 拉取 DSView 源码、固件、sigrok-cli、嵌入式 Python 和 MSYS2；`scripts/release.ps1` 只用本仓库和 `.deps/` 组装发布包，不再依赖本机的旧交付目录。
- DSView 源码从仓库根目录的 `dsview-132/` 移到 `.deps/dsview`，构建时自动应用 `patches/`。
- 发布包改名为 `logic-agent-dslogic-plus-v<版本>-win-x64.zip`，随包附带 FPGA bitstream、固件和 DreamSourceLab 的 `license.txt`。
- 发布包里的启动脚本从 `release.ps1` 内联字符串拆成 `packaging/launchers/`、`packaging/runtime/` 下的独立文件。
- `deploy-check.ps1` 在 `-SkipHardware` 时只要求 libsigrok4DSL 和资源目录能加载，不再因为没接设备而失败。

### Fixed
- `dslab` 打开设备时报 `activated unexpected device "Demo Device"`：`dslab_open()` 用过滤掉 Demo Device 后的序号去调 `ds_active_device_by_index()`，而库内列表里 Demo Device 排在前面，结果总是激活 Demo Device。现在按库内列表把序号换算后再激活。该问题由 4b7c979 引入；v1.0.0 包里的 `dslab.exe` 构建于该提交之前，所以没有暴露。

### Added
- `LICENSE`（MIT，覆盖 Python 部分）、`dslab/COPYING`（GPL-3.0）、`THIRD_PARTY.md`。
- `AGENTS.md`、`CONTRIBUTING.md`、GitHub Actions CI、`.gitattributes`。
- `scripts/build.ps1`：用 objdump 自动收集 `dslab.exe` 依赖的 DLL，并在干净 PATH 下做冒烟测试。
- `scripts/test.ps1 -RequireDecoders`：解码器测试被跳过时报错。

### Removed
- `build-portable.ps1`、`package-portable.ps1`、`bundle-dslab-runtime.ps1`、`vendor-decoders.ps1`、`plot-capture.ps1`，以及描述旧 `dist/dslogic-ai` 布局的文档。

## [1.0.0] - 2026-10-30

首个内部交付版本：DSLogic Plus 采集、22 类协议解码、MCP stdio 服务、Windows 便携包。
