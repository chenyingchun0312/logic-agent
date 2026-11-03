# npm 分发（`logic-agent-mcp`）

npm 包是「壳」：里面装着 `scripts/release.ps1` 产出的便携包原样（embeddable Python、
`dslab.exe`、`sigrok-cli` + 解码器、固件），外面加一层 Node 启动器和 `package.json`。
用户侧只需要 `npx -y logic-agent-mcp`，不装 Python、不配 `sigrok-cli`、不设环境变量。

```json
{ "mcpServers": { "logic-analyzer": { "command": "npx.cmd", "args": ["-y", "logic-agent-mcp"] } } }
```

## 为什么是壳包，而不是 wheel 或纯 JS

重活全在 `dslab.exe`（C，GPL）和 `sigrok-cli`（Python 3.4 + libsigrokdecode）这两个子进程里，
MCP 层只是编排。所以：

- 不重写 TS/JS：MCP 层重写没有收益，还要重做 22 类解码器的调用约定。
- 不做 wheel：pip 装不了 MSYS2 构建出的 `dslab.exe` 和 DSView 固件/`res`，也无法保证
  `sigrok-cli` 的 Python 3.4 运行时。

代价是包体：约 80 MB（压缩后），npm 单包解压上限 250 MB，够用。

## 包内容

| 路径 | 来源 | 说明 |
|---|---|---|
| `launcher/logic-agent-mcp.mjs` | `packaging/npm/launcher/` | 唯一入口，`bin` 指向它 |
| `runtime/python/` | python.org embeddable | `._pth` 已改成 `..\app` 在 `sys.path` 上 |
| `runtime/app/logic_agent/` | 本仓库 | MCP server、分析、归档 |
| `runtime/bin/` | `dslab/build` + DSView res + `.deps/sigrok/bin` | `dslab.exe`、运行库 DLL、`sigrok-cli.exe`、`res/` |
| `runtime/decoders/` | `.deps/sigrok/decoders` | libsigrokdecode 解码器树 |
| `runtime/licenses/` | 各许可证 | MIT / GPL / PSF |
| `firmware/` | DreamSourceLab | `DSLogicPlus.fw` + `license.txt` |
| `docs/`、`scripts/`、`*.cmd` | `packaging/` | 包内自检与文档 |
| `VERSION.txt`、`checksums.sha256`、`THIRD-PARTY-NOTICES.md`、`README.md`、`LICENSE` | 生成 | `checksums.sha256` 在打包时**按 npm 树重算** |

不带：`driver/`（公开包不附驱动）、`.deps/`、DSView 安装包、签名私钥。

## 本地构建与发布

```powershell
pwsh scripts/bootstrap.ps1                                   # 一次
pwsh scripts/build.ps1
pwsh scripts/test.ps1 -RequireDecoders
pwsh scripts/release.ps1 -NoDsviewDriver -NoDriver           # -> dist/stage/logic-agent
pwsh scripts/pack-npm.ps1 -SelfTest                          # -> dist/npm/logic-agent-mcp-<ver>.tgz
npm publish dist/npm/logic-agent-mcp-<ver>.tgz --access public
```

`pack-npm.ps1` 做的事，按顺序：

1. 预检必需文件（`runtime/python/python.exe`、`runtime/app/logic_agent/mcp_server.py`、
   `runtime/bin/dslab.exe`、`runtime/bin/sigrok-cli.exe`、`runtime/decoders/i2c/pd.py`、
   `THIRD-PARTY-NOTICES.md`）。缺任何一个默认直接失败——**装得上、跑不起来**是最糟的失败方式。
2. 把 `dist/stage/logic-agent` 复制成 `dist/npm/package`，删掉 `__pycache__` / `*.pyc`。
3. 由 `packaging/npm/package.json.in` 生成 `package.json`（`@@NAME@@`、`@@VERSION@@`）。
4. 放进 launcher，用 `packaging/npm/README.npm.md` 覆盖包内 README，补 `LICENSE`。
5. 按 npm 树重算 `checksums.sha256`（否则 `VERIFY-FILES.cmd` 会因 README 变化报 MISMATCH）。
6. `-SelfTest`：跑 `node launcher --version`，再用真实 stdin 发一次 `initialize`，确认
   stdout 上拿到 JSON-RPC 响应、且没有 Traceback。
7. `npm pack` 出 tgz。

快速迭代（不需要完整 bundle，验证 launcher 与打包逻辑）：

```powershell
pwsh scripts/pack-npm.ps1 -Stage build\fake-stage -SkipVerify -SelfTest
```

`-SkipVerify` 只是跳过文件预检并降级为警告；`launcher` 在没有内置 Python 时会回退到
`PATH` 上的 `python`，配合 `PYTHONPATH=runtime/app` 仍能完成 `--version` 与 `initialize`。

## 版本与 tag

- 版本唯一来源是 `logic_agent/__init__.py:__version__`，`pack-npm.ps1` 和 workflow 都从这里读。
- tag 必须是 `v<version>`；workflow 第一步就校验，不一致直接失败。
- npm **不能复用已发布的版本号**（`npm unpublish` 有时限且不可靠）。版本发错了就发下一个补丁版本。

## GitHub Actions

`.github/workflows/npm-publish.yml`：

- 触发：push tag `v*`；或手动 `workflow_dispatch`（`publish` 勾上才发 npm，否则只出 artifact）。
- 顺序：校验 tag/版本 → 校验 `deps.json` 已固定 sigrok-cli → `bootstrap` → `build` →
  `test -RequireDecoders` → `release` → `pack-npm -SelfTest` → 上传 `npm-package` artifact →
  （tag 或 `publish`）`npm publish --provenance`。
- 需要的仓库配置：secret `NPM_TOKEN`（npm automation token，publish 权限）。
  `permissions: id-token: write` 已写在 workflow 里，供 provenance 用。
- 缓存 `.cache` + `.venv`（按 `deps.json` 哈希）。`.deps/` 不缓存：MSYS2 用
  `msys2/setup-msys2` 步骤提供，不要让它进 cache。

### 一次性前置：把 sigrok-cli 固定下来

`sigrok-cli` 上游只发布 NSIS 安装包（没有 zip，也没有 GitHub 二进制 release），而 nightly
每次重建的版本号都还是 `0.8.0-git-f44dd91`，所以**上游 URL 本身不能当 pin**。仓库的做法是把
安装包拆开、重打包成最小运行时 zip，再钉 `sha256`。这一步由 `sigrok runtime` workflow 完成，
任何人的机器上都不需要装过 sigrok-cli：

```
Actions -> sigrok runtime -> Run workflow
  tag      : sigrok-runtime-20261103
  upstream : 留空则用 deps.json 的 sigrok_cli.upstream.url
```

它依次：下载上游安装包 → 7-Zip 解开 → 规范成 `bin/` + `decoders/` + `VERSION.txt` →
补上 MSVC 2010 x64 运行时（`deps.json` 的 `vcredist`）→ 产出
`sigrok-cli-0.8.0-git-f44dd91-20261103-win64.zip` → 传成 release asset → `-UpdateDeps`
回填 `deps.json` 并提交。

`vcredist` 这一步不能省：`sigrok-cli.exe` 只导入 `python34.dll`，但 `python34.dll` 导入
`MSVCR100.dll`，而上游安装包并不带这个 DLL。缺了它，`sigrok-cli.exe` 在没有装 VC++ 2010
运行时的机器上会直接 `exit 0xC0000135`（DLL not found），版本探测也是一片空白。

下载、解包、重打包、上传、回填全在 runner 上完成：开发者机器不需要装过 sigrok-cli，
也不需要先下载或解包安装包。workflow 会自己准备 7-Zip（runner 没带就装），所以这一步
不依赖任何本机环境。

`-UpdateDeps` 会按 origin 远端拼出 `https://github.com/<owner>/<repo>/releases/download/<tag>/<zip>`，
回填 `version`/`url`/`sha256`，同时把安装包来源记进 `upstream.url`/`upstream.sha256`——
`bootstrap.ps1` 只认这个稳定 HTTPS 地址和 sha256 两样。

脚本会检查 zip 里确实有 `bin/sigrok-cli.exe`、`bin/python34.dll`、`bin/python34.zip`、
`decoders/i2c/pd.py`，并报告解码器数量（少于 10 个直接失败）。

## 供应链

- **没有 `postinstall` / `preinstall`**：装完即可离线用；`package.json` 里没有任何生命周期脚本。
- launcher **永不写 stdout**（stdout 是 JSON-RPC 帧），诊断只走 stderr。
- `npm publish --provenance` 生成可验证的构建来源；tarball 的完整性由 registry 的
  `integrity` 字段保证。
- 包内 `checksums.sha256` 覆盖 npm 树里的每个文件，`VERIFY-FILES.cmd` 可离线自检。

## 排错

| 现象 | 处理 |
|---|---|
| 客户端起不来，日志里是 `spawn npx ENOENT` | Windows 上 `command` 写 `npx.cmd`；或改成 `cmd /c npx -y logic-agent-mcp` |
| 装到了旧版本 | `npx -y logic-agent-mcp@latest`（`npx` 有本地缓存） |
| `EBADPLATFORM` | 包只在 `win32`/`x64` 上发布，macOS/Linux 会直接拒绝安装 |
| `cannot start the Python server` + `python: python` | `runtime/python` 缺失（装包不完整），或 `LOGIC_AGENT_PYTHON` 指错了 |
| 第一次启动卡几十秒 | 正常，在下载约 80 MB |
| 能启动但采不到数据 | 驱动没装：装一次 DSView，或 `INSTALL-DRIVER.cmd`；设备是独占 USB，别让两个客户端同时开 |
| `checksums` 报 MISMATCH | 用了被手工改过的包目录；重装，别改 `runtime/` |

## 许可证

`package.json` 写 `"license": "MIT AND GPL-3.0-or-later"`：包是不同许可证组件的**聚合**。

- `launcher/`、`runtime/app/logic_agent/` —— MIT（本仓库）。
- `runtime/bin/dslab.exe` 及其 MinGW 运行库、`runtime/bin/sigrok-cli.exe`、`runtime/decoders/`
  —— GPL-3.0-or-later，对应源码见本仓库该 tag、`deps.json` 固定的 DSView 提交与 `patches/`。
- `runtime/python/` —— PSF；`firmware/` —— DreamSourceLab（MIT 风格，见 `license.txt`）。

`logic_agent` 只通过子进程调用 GPL 可执行文件，不 import、不链接 GPL 代码，所以这一层保持 MIT。
