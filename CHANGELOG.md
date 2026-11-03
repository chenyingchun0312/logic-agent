# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
版本号只在 `logic_agent/__init__.py` 中维护，`pyproject.toml` 和发布脚本都从那里读取。

## [Unreleased]

### Added
- `scripts/pack-npm.ps1` + `packaging/npm/`：把发布包原样打成 npm 包（`npx -y logic-agent-mcp`）。Node 启动器只转发 stdio、绝不写 stdout；打包时按 npm 树重算 `checksums.sha256`；`-SelfTest` 会真跑 `--version` 和一次 `initialize`。客户端不再需要绝对路径。
- `.github/workflows/npm-publish.yml`：tag `v<version>` 触发，先校验 tag 与 `__version__` 一致、`deps.json` 已固定 sigrok-cli，再跑 bootstrap/build/test/release/pack，最后 `npm publish --provenance`；手动触发可只出 artifact 不发包。
- `scripts/pack-sigrok-cli.ps1`：从上游 sigrok.org 安装包（NSIS，用 7-Zip 解开）重新 mint `sigrok-cli-<版本><日期>-win64.zip` 并算 sha256；`-UpdateDeps` 直接回填 `deps.json`（`url` 按 origin 远端推导，同时记录 `upstream.url`/`upstream.sha256`）。补上了 `deps.json` 和 `bootstrap.ps1` 一直引用但并不存在的脚本。
- `.github/workflows/sigrok-runtime.yml`：手动触发，下载上游安装包 → 重打包 → 传成 release asset → 提交 `deps.json` 的 pin。上游 nightly 每次重建版本号不变，所以 pin 必须由这个 workflow 生成，不能直接用上游 URL。
- `docs/npm.md`：npm 包形态、包内容、构建/发布步骤、CI 的一次性前置（固定 sigrok-cli）、供应链与排错。
- `scripts/lib/common.ps1`：`Copy-SigrokRuntime` 增加 `-SkipVersionProbe`，用于在无法执行该二进制的机器上打包（bootstrap 默认行为不变）。

### Changed
- `scripts/bootstrap.ps1` 不再从本机拷贝 sigrok-cli：删掉 `-SigrokRoot` 参数和 `Find-SigrokRoot` 的全部查找路径（`SIGROK_CLI_ROOT`、`.deps/sigrok-cli`、`%ZEPHYR_ENV_HOME%\sigrok-cli`、`C:\Program Files\sigrok\sigrok-cli`），只从 `deps.json` 的固定 `sigrok_cli.url` 下载并校验 sha256。`url` 为空时直接报错并给出 mint 步骤，不再静默回退本机安装。
- `scripts/lib/common.ps1`：`Find-SigrokRoot` 换成 `Find-7Zip` + `Expand-NsisInstaller`（7-Zip 解 NSIS），并新增 `Get-JsonObjectSpan` / `Set-JsonScalar` 用于只改 `deps.json` 里 `sigrok_cli` 自己的键、不碰嵌套的 `upstream`。
- `scripts/pack-sigrok-cli.ps1` 只接受 https 安装包地址：删掉 `-FromDirectory` 和本地 `.exe` 分支，下载、解包、重打包、上传、回填全部在 runner 上完成；`sigrok runtime` workflow 自备 7-Zip，mint 不依赖任何本机环境。
- `docs/npm.md`、`docs/upstream-sync.md`、`CONTRIBUTING.md`：sigrok 升级流程改成「跑 `sigrok runtime` workflow 重新 mint」。CI 与贡献者都不再需要本机装 sigrok-cli。

## [1.2.0] - 2026-11-03

### Added
- `logic-agent mcp-install`（发布包里的 `INSTALL-MCP.cmd`）：一条命令把 `logic-analyzer` 加进本机的 Codex、DeepSeek Harness、Claude Code、Claude Desktop、GitHub Copilot（VS Code / CLI）、Cursor、Kiro、Windsurf 和 Cline。默认只处理检测到的客户端，合并写入（其它 server 原样保留），配置无法解析时报告且不覆盖，写完用真实 `initialize` + `capabilities` 验证启动。支持 `--list`、`--dry-run`、`--remove`、`--scope project`、`--json`、`--backend`、`--data-dir`。
- `docs/mcp-clients.md`（发布包内 `docs\MCP-CLIENTS.md`）：客户端配置位置、手动 JSON/TOML、常见排错。
- `deepseek-harness`：DeepSeek Harness 的 MCP 配置是 `~/.dsh/profiles/desktop/cordis.patch.yml` 这个 Cordis YAML 补丁层，安装器维护一个带标记的 `@deepseek-ai/dsh-mcp-client` 条目；已经有手写条目时原地接管并备份，重复运行只替换标记块，`--remove` 只删这一块，文件顶层不是 YAML 列表时拒绝写入。
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
