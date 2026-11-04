# logic-agent 介绍与上手

> 一句话：把逻辑分析仪接进 AI Agent 的工具集（MCP server），让 Agent 能采集、解码、测量，
> 但**原始波形永远不进模型上下文**。

## 1. 它解决什么问题

直接把几十万点的波形丢给模型，上下文会瞬间爆掉，结论也不可靠。这里换一种做法：

- 采集结果落盘，只把**有界摘要**返回给 Agent：通道统计、最多 N 个边沿、解码出来的协议帧；
- 每次采集是一个不可变的 `capture_id`，需要复现时按 ID 回读；
- Agent 按 `capabilities → capture → analyze_window → decode_*` 的路径逐步缩小范围，而不是一次看全。

所以它适合“帮我看看这路 I²C 到底有没有 ACK”“这段 SPI 的时钟是多少”这类问题，不适合长时间流式抓取。

## 2. 现在能做什么

### 支持的后端

| 后端 | 说明 |
|---|---|
| `dslab` | DSLogic Plus（DreamSourceLab），默认后端，Windows |
| `sigrok` | `sigrok-cli` 支持的设备 |
| `mock` | 确定性合成波形，**没有硬件也能验证整条链路** |

### 10 个 MCP 工具

| 工具 | 作用 |
|---|---|
| `capabilities` | 报告可用后端、安全上限、推荐工作流（任何会话的第一步） |
| `list_devices` | 发现已连接设备，不启动采集 |
| `self_test` | 用 DSLogic 内部图案发生器做硬件自检，先分清“采集链路坏了”还是“信号没进来” |
| `capture` | 采集并归档，返回每通道的边沿数、脉宽、粗略频率 |
| `list_captures` | 列出历史采集（最新在前） |
| `analyze_window` | 对某个时间窗做放大分析，`max_edges` 控制返回量 |
| `decode_uart` | UART 解码，会显式返回 framing/parity 错误 |
| `decode_protocol` | 22 类单层/堆叠协议解码，通道按角色映射（如 `{"scl": "D0", "sda": "D1"}`） |
| `measure_period` | 周期、频率、占空比、抖动 |
| `import_csv` | 导入 DSView 导出的 CSV，直接分析别人抓的数据 |

另外 `resources/read` 可以读取某次采集的 manifest（文件路径、SHA-256、采样参数）。

### 22 类协议

`i2c` `spi` `qspi` `uart` `i2s` `can` `lin` `modbus` `mdio` `ps2` `jtag` `jtag_ejtag`
`jtag_stm32` `swd` `cjtag` `spiflash` `sdcard_sd` `sdcard_spi` `onewire_link`
`onewire_network` `ir_nec` `spdif`

多层可以叠（例如 SPI Flash 叠在 SPI 之上），`include_lower_layers` 决定是否带上下层注释。

### 安全与边界

| 限制 | 值 |
|---|---|
| 通道数 | ≤ 16 |
| 采样率 | ≤ 50 MS/s |
| 单次时长 | ≤ 60 s |
| 总采样点 | ≤ 500 万 |
| 单次采集落盘 | ≤ 100 MB |
| 单个工具返回 | ≤ 64 K 字符 |

- 只调用固定的可执行文件和固定参数结构；`capture_id`、资源路径做严格校验。
- 固件写入**只打印命令**，不会自动执行；正常设备不需要刷固件。
- 逻辑分析仪只能承受额定电压，测 5 V 以上或差分总线前必须加隔离/电平转换。

## 3. 安装（约 5 分钟）

### 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11 x64（包只声明 `win32`/`x64`，macOS/Linux 会直接拒绝安装） |
| Node | ≥ 18.17 |
| 硬件 | DSLogic Plus（可选，没有也能用 `mock` 后端） |
| 驱动 | 需要装一次（见下） |

### 第 1 步：装 USB 驱动

npm 包**不带驱动**。装一次 [DSView](https://www.dreamsourcelab.com/download/)，或用 Zadig 给设备装
libusb/WinUSB。装完插上设备，设备管理器里能看到 DSLogic 即可。

### 第 2 步：验证包能跑

```powershell
npx -y logic-agent-mcp --version
```

输出当前版本号（如 `1.2.1`）就说明运行时（内置 Python、`dslab.exe`、`sigrok-cli`、解码器）都齐了。
首次会下载约 35 MB，之后走 npm 缓存。

### 第 3 步：写进 MCP 客户端

所有客户端都是同一条命令（DeepSeek Harness 例外，它的配置是 YAML，见下），**Windows 上 `command`
必须写 `npx.cmd`**（客户端不经过 shell，写 `npx` 会报 `spawn ENOENT`）：

```json
{ "mcpServers": { "logic-analyzer": {
    "command": "npx.cmd",
    "args": ["-y", "logic-agent-mcp"]
} } }
```

各客户端配置文件位置：

| 客户端 | 配置文件 |
|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` |
| Claude Code | `~/.claude.json`（项目级 `.mcp.json`） |
| VS Code Copilot | `%APPDATA%\Code\User\mcp.json`（键是 `servers`，且要 `"type": "stdio"`） |
| DeepSeek Harness | `~/.dsh/profiles/desktop/cordis.patch.yml`（**YAML 补丁层，不是 JSON**，见下） |
| Codex CLI | `~/.codex/config.toml`（见下方 TOML） |
| Kiro | `~/.kiro/settings/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Cline | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` |

Codex 用 TOML：

```toml
[mcp_servers.logic_analyzer]
command = "npx.cmd"
args = ["-y", "logic-agent-mcp"]
```

VS Code 是 `servers` + `type`：

```json
{ "servers": { "logic-analyzer": {
    "type": "stdio",
    "command": "npx.cmd",
    "args": ["-y", "logic-agent-mcp"]
} } }
```

### DeepSeek Harness（YAML 补丁层）

DSH 的 MCP 配置不在 JSON 里，而是 `~/.dsh/profiles/desktop/cordis.patch.yml` 这个 **Cordis 补丁层**
（顶层是 YAML 数组）。往里加一条：

```yaml
- insert:
    - id: mcp-logic-agent
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: logic
        transport: stdio
        command: "npx.cmd"
        args: ["-y", "logic-agent-mcp"]
        toolCallTimeoutMs: 120000
        failOnStartupError: false
```

- 工具名会变成 `mcp__logic__capture` 这种形式（由 `serverName: logic` 决定）。
- 改完要**重启 DeepSeek Harness**（或新建会话），配置只在启动时加载。
- 已经手写过 `@deepseek-ai/dsh-mcp-client` 条目时不要再加第二条，否则会出现两个 server；
  有便携包或源码检出的话，`logic-agent mcp-install deepseek-harness` 会原地接管并备份成
  `cordis.patch.yml.bak`。
- 这个文件顶层必须是 YAML 数组；安装器遇到映射会拒绝写入，手改也请保持数组形式。

改完**完全退出并重启客户端**（不是重开窗口），再确认工具列表里出现 `logic-analyzer`。

## 4. 第一次怎么用

### 先跑一遍冒烟

在对话里直接说：

```text
先调用 capabilities，确认后端和限制；然后 list_devices 看看有没有设备。
```

没有硬件时，可以让它走 mock 后端验证整条链路：

```text
用 backend=mock、通道 D0、1 MHz、1 ms 采一次，然后报告每个通道的边沿数和频率。
```

### 常见对话模板

| 你想做的事 | 让 Agent 做什么 |
|---|---|
| 确认工具链没问题 | `capabilities` → `list_devices` → `self_test` |
| 抓一段总线 | `capture {channels:["D0","D1"], sample_rate_hz:1000000, duration_s:0.01}` |
| 只看某个位置 | `analyze_window {capture_id, channel:"D0", start_s:0.002, duration_s:0.001, max_edges:50}` |
| 解 I²C | `decode_protocol {capture_id, protocol:"i2c", channels:{scl:"D0", sda:"D1"}}` |
| 解串口 | `decode_uart {capture_id, channel:"D0", uart:{baud:115200}}` |
| 看时钟 | `measure_period {capture_id, channel:"D0"}` |
| 分析别人给的波形 | `import_csv {path:"D:\\data\\export.csv", channels:["D0"]}` |

注意 `decode_*` 和 `measure_period` 都作用在**已保存的采集**上，所以必须先有 `capture_id`（或先 `import_csv`）。

建议在 Agent 的系统提示里加一条约束：

> 任何协议结论必须引用一次成功的确定性解码，或明确标注为“基于波形统计的推测”。

### 5 分钟验收清单

- [ ] `npx -y logic-agent-mcp --version` 打印出版本号
- [ ] 客户端工具列表里出现 `logic-analyzer`（或 `mcp__logic-analyzer__*`）
- [ ] `capabilities` 返回后端列表和 limits
- [ ] `backend=mock` 采一次，返回每通道边沿统计
- [ ] 接上设备后 `list_devices` 能看到设备，`self_test` 能跑通
- [ ] 真实信号 `capture` + `decode_protocol`/`decode_uart` 能出帧

## 5. 数据落在哪

- npm 包默认写到 `%LOCALAPPDATA%\logic-agent`，每次采集一个子目录，附 manifest 和 SHA-256。
- 想换位置：客户端配置里加 `--data-dir D:\data\logic-agent`。
- 清理：直接删对应目录；采集是不可变的，不存在“改坏”的问题。

## 6. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `spawn npx ENOENT` | Windows 上 `command` 要写 `npx.cmd` |
| PowerShell 提示“因为在此系统上禁止运行脚本” | 执行策略拦住了 `npm.ps1`/`npx.ps1`；命令改用 `npm.cmd`/`npx.cmd`，或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `EBADPLATFORM` | 包只在 `win32`/`x64` 发布 |
| 第一次启动卡几十秒 | 正常，首次要下载约 35 MB |
| 能启动但采不到数据 | 驱动没装；设备是独占总线，别让两个客户端同时采集 |
| 装完还是旧版本 | `npx -y logic-agent-mcp@latest`（`npx` 有缓存） |
| 客户端列表里没有这个 server | 配置写错层级（VS Code 是 `servers`、Codex 是 TOML），或没完全重启客户端 |

## 7. 目前还不支持

Saleae 后端、CAN CRC 校验、触发失败自动重试、长时间流式采集；`dslab` 只支持 Windows。

## 8. 继续读

- 安装细节、供应链与排错：[`npm.md`](npm.md)
- 各客户端配置与手动写法：[`mcp-clients.md`](mcp-clients.md)
- 协议解码怎么选通道：[`protocol-decoding.md`](protocol-decoding.md)
- 架构与设计取舍：[`architecture.md`](architecture.md)
- 版本变化：[`../CHANGELOG.md`](../CHANGELOG.md)
