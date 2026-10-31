# logic-agent：让 AI Agent 使用逻辑分析仪

把逻辑分析仪变成 AI Agent 可调用的**受限工具集**（MCP），而不是把整段原始波形塞进模型上下文。

- 支持 **DSLogic Plus**（`dslab` 后端），以及 `sigrok-cli` 支持的设备和一个确定性的 `mock` 后端
- 每次采集归档为不可变的 `capture_id`，原始数据留在磁盘，附 manifest 和 SHA-256
- 工具只返回通道统计、有限个边沿和解码事件
- 22 类单层或堆叠协议解码（I²C、I²S、SPI、QSPI、CAN、JTAG、SWD、SPI Flash、Modbus 等），UART 解码会显式返回 framing/parity 错误
- 周期、频率、占空比、抖动测量；可导入 DSView 导出的 CSV
- 采样率、时长、通道数和返回字符数都有硬上限

## 使用发布包（不需要开发环境）

1. 从 [Releases](../../releases) 下载 `logic-agent-dslogic-plus-v<版本>-win-x64.zip` 和同名 `.sha256`。
2. 解压到英文路径，例如 `D:\tools\logic-agent`。
3. 依次运行 `VERIFY-FILES.cmd`、`CHECK.cmd`、`SELF-TEST.cmd`，都应输出顶层 `"ok": true`。设备不认时先运行 `INSTALL-DRIVER.cmd`。
4. 在 MCP 客户端里配置：

```toml
# Codex: ~/.codex/config.toml
[mcp_servers.logic_analyzer]
command = "cmd"
args = ["/c", "D:/tools/logic-agent/MCP.cmd", "--backend", "dslab", "--data-dir", "D:/tools/logic-agent/data/captures"]
```

```json
// Claude Desktop / Kiro / 其他 MCP 客户端
{ "mcpServers": { "logic-analyzer": {
    "command": "cmd",
    "args": ["/c", "D:/tools/logic-agent/MCP.cmd", "--backend", "dslab", "--data-dir", "D:/tools/logic-agent/data/captures"]
} } }
```

包内的 `docs/INSTALL.md`、`DRIVER.md`、`FIRMWARE.md`、`TROUBLESHOOTING.md` 有详细说明。

## 从源码构建

需要 Windows 10/11 x64、[PowerShell 7](https://github.com/PowerShell/PowerShell/releases)、Git 和 Python ≥ 3.11。

```powershell
git clone <repo-url> logic-agent
cd logic-agent
pwsh scripts/bootstrap.ps1             # 拉取 DSView、固件、sigrok-cli、Python、MSYS2 到 .deps/
pwsh scripts/build.ps1                 # 编译 dslab/build/dslab.exe
pwsh scripts/test.ps1 -RequireDecoders # 跑全部测试，不允许 skip
pwsh scripts/release.ps1               # 生成 dist/*.zip
```

所有第三方输入的版本和 SHA-256 都固定在 [`deps.json`](deps.json)。只改 Python 代码时不需要编译 C。内网镜像、离线环境和贡献流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；给 AI 编码代理的约定见 [`AGENTS.md`](AGENTS.md)。

不接 MCP 也能直接试用：

```powershell
.venv\Scripts\python -m logic_agent.cli demo                 # mock 后端，无硬件
.venv\Scripts\python -m logic_agent.cli --backend dslab capture --channels D0 --sample-rate 1000000 --duration 0.01
```

## 推荐给 Agent 的工作流

1. `capabilities` / `list_devices` / `self_test`：确认后端、通道名和采集链路，不要凭猜测填 `D0`。
2. `capture`：先采短窗口（1–20 ms），看 `hints` 和通道统计。
3. `analyze_window`：只对可疑区间放大，限制 `max_edges`。
4. `decode_uart` / `decode_protocol` / `measure_period`：用确定性解码器核对，见 [`docs/protocol-decoding.md`](docs/protocol-decoding.md)。
5. `resources/read`：需要复现时读取 manifest，定位原始文件和校验和。

建议在 Agent 的系统提示里加一条：

> 任何协议结论必须引用一次成功的确定性解码，或明确标注为"基于波形统计的推测"。

## 架构

```text
AI Agent (Codex / Claude / Kiro ...)
   │ MCP stdio
   ▼
logic_agent  (Python 标准库，MIT)
   ├─ 采集 → 校验 → 归档 (.logic_agent_data/captures/<capture_id>/)
   ├─ 窗口统计 / 周期测量 / UART
   └─ 协议解码 → sigrok-cli + libsigrokdecode (子进程)
   │
   ├─ DslabBackend  → dslab.exe (C, GPL-3.0, 静态链接 DSView libsigrok4DSL) → DSLogic Plus
   ├─ SigrokBackend → sigrok-cli
   └─ MockBackend
```

设计取舍见 [`docs/architecture.md`](docs/architecture.md)，DSLogic Plus 的固件、USB PID 和已修复问题见 [`docs/dslogic-plus.md`](docs/dslogic-plus.md)。

## 安全与边界

- 默认上限：16 通道、50 MS/s、60 s、500 万采样点、100 MB。
- 命令只调用固定的可执行文件和固定参数结构；`capture_id` 严格校验，资源路径做目录逃逸检查。
- 工具错误返回结构化的 `{ok: false, error, message, details}`。
- 固件写入只会打印命令，不会自动执行；正常设备不需要刷固件。
- 逻辑分析仪只能承受额定电压，测 5 V 以上或差分总线前必须加隔离或电平转换。

当前未实现：Saleae 后端、CAN CRC 校验、触发后自动重试、长期流式采集。`dslab` 目前只支持 Windows，欢迎贡献 Linux 移植。

## 维护

- 发布流程：[`docs/release-process.md`](docs/release-process.md)
- DSView / 解码器升级：[`docs/upstream-sync.md`](docs/upstream-sync.md)
- 变更记录：[`CHANGELOG.md`](CHANGELOG.md)

## 许可证

`logic_agent/` 等 Python 部分为 [MIT](LICENSE)；`dslab/` 和 `patches/` 为 GPL-3.0-or-later；`decoders_ext/` 为 GPL-2.0-or-later。发布包中的第三方组件（DSView 资源和固件、sigrok-cli、CPython、MSYS2 运行库）见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

DSLogic 和 DSView 是 DreamSourceLab 的商标，本项目与 DreamSourceLab 无隶属关系。
