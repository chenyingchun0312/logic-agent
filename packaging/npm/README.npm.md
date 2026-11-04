# logic-agent-mcp

让 AI agent 直接驱动逻辑分析仪（DSLogic Plus 优先）的 **MCP server**。
采集、解码、分析都在本地完成，**原始采样数据永不进入模型上下文**——agent 只看到
有界的小结果（边沿摘要、解码帧、协议统计、截图级别的事件列表）。

这个 npm 包是「壳」：里面装着完整的 Windows x64 运行时（embeddable Python、
`dslab.exe`、`sigrok-cli` + 解码器、固件），用 Node 启动器拉起 Python MCP server。
不需要 `pip install`，不需要自己配 `sigrok-cli`，也不需要设一堆环境变量。

## 快速开始

```powershell
npx -y logic-agent-mcp --version
```

装进 MCP 客户端后就能用了（Windows 上 `command` 建议写 `npx.cmd`，见下）。

### 硬件准备

- **USB 驱动**：装一次 [DSView](https://www.dreamsourcelab.com/download/)（或用 Zadig 装
  libusb/WinUSB）。包内不带驱动。
- **固件**：包内已带 `firmware/DSLogicPlus.fw`，需要刷新时让 agent 调用固件工具
  （它只会打印建议命令，不会自动写固件）。
- 设备是独占的 USB 资源：同时只让一个客户端/一次采集占用它。

### 没有硬件也能跑

```powershell
npx -y logic-agent-mcp --backend mock
```

`mock` 后端提供合成波形，用来验证客户端配置、试解码器、跑冒烟测试。

## 配置 MCP 客户端

客户端配置统一是「用 npx 拉起 stdio server」，不需要绝对路径：

```json
{
  "mcpServers": {
    "logic-agent": {
      "command": "npx.cmd",
      "args": ["-y", "logic-agent-mcp"]
    }
  }
}
```

- **Claude Desktop** —— `%APPDATA%\Claude\claude_desktop_config.json`
- **Cursor** —— `%USERPROFILE%\.cursor\mcp.json`
- **Codex** —— `%USERPROFILE%\.codex\config.toml`：

  ```toml
  [mcp_servers.logic-agent]
  command = "npx.cmd"
  args = ["-y", "logic-agent-mcp"]
  ```

- **VS Code** —— 工作区 `.vscode/mcp.json`，`servers` 键下用 `"type": "stdio"`：

  ```json
  {
    "servers": {
      "logic-agent": {
        "type": "stdio",
        "command": "npx.cmd",
        "args": ["-y", "logic-agent-mcp"]
      }
    }
  }
  ```

启动器**从不向 stdout 写任何东西**，stdout 完整留给 JSON-RPC，诊断信息只走 stderr。
所以客户端日志里看到的 stderr 输出是正常的，不是错误。

如果某个客户端不接受 `.cmd` 直接作为 command，用 `cmd /c npx -y logic-agent-mcp` 代替。
更多客户端与排错见仓库的 `docs/mcp-clients.md`。

## 运行时行为

| 项目 | 值 |
|---|---|
| 数据目录 | `%LOCALAPPDATA%\logic-agent`（采集存在 `captures\` 下） |
| 首次启动 | npx 下载整包（约 35 MB），之后走 npm 缓存 |
| 网络 | 除了首次下载，运行时**不需要**网络 |
| Node | ≥ 18.17 |
| 平台 | Windows x64 |

### 环境变量（都可选）

| 变量 | 作用 |
|---|---|
| `LOGIC_AGENT_DATA_DIR` | 覆盖数据目录（等价于 `--data-dir`） |
| `LOGIC_AGENT_BACKEND` | `dslab`（默认）/ `sigrok` / `mock` |
| `LOGIC_AGENT_DECODER_EXE` | 覆盖 `sigrok-cli.exe` 路径 |
| `SIGROKDECODE_DIR` | 覆盖解码器目录 |
| `LOGIC_AGENT_PYTHON` | 用指定的 Python 代替内置解释器（自备 Python ≥ 3.11 时） |
| `DSLAB_EXE` / `DSLAB_RES_DIR` | 覆盖 `dslab.exe` 与它的 `res/` 资源目录 |

## 从源码构建这个包

```powershell
pwsh scripts/bootstrap.ps1      # 一次；拉依赖并建 .venv
pwsh scripts/build.ps1          # 编译 dslab.exe
pwsh scripts/release.ps1 -NoDsviewDriver -NoDriver
pwsh scripts/pack-npm.ps1 -SelfTest
npm publish dist/npm/logic-agent-mcp-<version>.tgz --access public
```

发布流水线见仓库 `.github/workflows/npm-publish.yml`，细节见 `docs/npm.md`。

## 许可证

本包整体按 **MIT AND GPL-3.0-or-later** 分发，因为它是两部分的聚合：

- `launcher/`（Node 启动器）与 `runtime/app/logic_agent/`（MCP server、分析代码）—— **MIT**。
- `runtime/bin/dslab.exe` 及其运行库、`runtime/bin/sigrok-cli.exe`、`runtime/decoders/`
  —— GPL-3.0-or-later，对应源码与构建方式见 `THIRD-PARTY-NOTICES.md` 与仓库
  `dslab/`、`decoders_ext/`、`patches/`、`deps.json`。
- `firmware/` —— DreamSourceLab 的固件与许可（`DSLogicPlus.fw` + `license.txt`）。

`dslab` 与 `sigrok-cli` 是独立可执行文件，Python 层只通过子进程调用它们，不链接 GPL 代码。

## 链接

- 源码与文档：<https://github.com/chenyingchun0312/logic-agent>
- 问题反馈：<https://github.com/chenyingchun0312/logic-agent/issues>
