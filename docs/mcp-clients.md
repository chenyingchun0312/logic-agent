# MCP 客户端接入

目标：一条命令把 `logic-analyzer` 加进本机所有支持 MCP 的 Agent，并且当场验证它能启动、能回答 `capabilities`。

## 一条命令

发布包（解压后双击即可，不需要 Python）：

```text
INSTALL-MCP.cmd
```

源码检出：

```powershell
.venv\Scripts\python.exe -m logic_agent.cli mcp-install
```

它会按顺序做三件事：

1. **检测**本机装了哪些客户端（配置目录、配置文件和 `PATH` 上的 CLI 都算）；
2. **合并**写入每个客户端的配置文件：只增删 `logic-analyzer` 这一条，其它 server、其它字段原样保留；配置无法解析时报告并**不写**；
3. **验证**：用写入的启动命令真实拉起一次 MCP 服务，发 `initialize` 和 `capabilities`，确认返回里有 `logic-agent` 和后端状态。

输出示例：

```text
logic-agent MCP setup
  mode    : bundle (D:\tools\logic-agent)
  command : D:\tools\logic-agent\runtime\python\python.exe -m logic_agent.mcp_server --data-dir D:\tools\logic-agent\data\captures

Install
  codex           added    C:\Users\me\.codex\config.toml
  vscode          added    C:\Users\me\AppData\Roaming\Code\User\mcp.json
  kiro            added    C:\Users\me\.kiro\settings\mcp.json

Verified: the server starts and answers capabilities (dslab=yes, sigrok=no, mock=yes; 22 protocols).

Next: restart the app, then ask the agent to call the "capabilities" tool.
  - restart Codex, then /mcp (or `codex mcp list`)
```

## 支持的客户端

| `id` | 客户端 | 用户级配置 | 项目级配置 |
|---|---|---|---|
| `codex` | Codex CLI | `~/.codex/config.toml`（`[mcp_servers.logic_analyzer]`） | — |
| `claude-code` | Claude Code | `~/.claude.json` | `.mcp.json` |
| `claude-desktop` | Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | — |
| `copilot-cli` | GitHub Copilot CLI | `~/.copilot/mcp-config.json`（`COPILOT_HOME` 优先，含 `"type": "local"` 和 `"tools": ["*"]`） | `.mcp.json` |
| `vscode` | VS Code Copilot | `%APPDATA%\Code\User\mcp.json`（`servers` + `"type": "stdio"`） | `.vscode/mcp.json` |
| `cursor` | Cursor | `~/.cursor/mcp.json` | `.cursor/mcp.json` |
| `kiro` | Kiro | `~/.kiro/settings/mcp.json` | — |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` | — |
| `cline` | Cline（VS Code 扩展） | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` | — |

JSON 客户端里的名字是 `logic-analyzer`（会变成 `mcp__logic-analyzer__capture` 这样的工具前缀）；Codex 的 TOML 表沿用 `logic_analyzer`，这样已经按旧文档配置过的用户不会被写重复条目。

客户端的配置文件位置随版本变化。以 `logic-agent mcp-install --list` 打印的路径为准；那份输出就是本工具这次会写的文件。

## 常用参数

```powershell
logic-agent mcp-install --list                 # 只检测：装了哪些客户端、配置写到哪
logic-agent mcp-install --dry-run              # 只打印可粘贴的 JSON/TOML，不动任何文件
logic-agent mcp-install --all                  # 没检测到的客户端也写（例如配置目录还没创建）
logic-agent mcp-install codex vscode           # 只写指定的客户端
logic-agent mcp-install --scope project        # 写到当前目录的项目级配置（.mcp.json、.vscode/mcp.json）
logic-agent mcp-install --remove               # 删除这一条，其它 server 保留
logic-agent mcp-install --backend sigrok       # 固定后端；默认由服务自己挑（dslab > sigrok > mock）
logic-agent mcp-install --data-dir D:\cap      # 采集归档目录，默认 <包目录>\data\captures
logic-agent mcp-install --json                 # 机器可读结果，便于脚本判断
logic-agent mcp-install --no-verify            # 跳过启动验证
```

退出码：`0` 全部成功；`2` 有客户端配置无法解析或有其它错误（每个客户端单独报告，其余照常写入）。

## 写入的启动命令

客户端直接 spawn argv，不经过 `cmd.exe`，所以没有批处理引号和空格路径的坑：

```text
D:\tools\logic-agent\runtime\python\python.exe -m logic_agent.mcp_server --data-dir D:\tools\logic-agent\data\captures
```

- `dslab.exe`、`sigrok-cli.exe`、解码器目录都由包内相对布局自动发现，配置里**不需要** `DSLAB_EXE`、`SIGROKDECODE_DIR` 这类环境变量。
- 源码检出时命令是当前的 Python 解释器，并带上 `PYTHONPATH=<仓库根>`，所以从任意工作目录启动都能 `import logic_agent`；数据目录默认 `~/.logic_agent_data`。
- 没有写 `--backend`：服务自己选（有 `dslab.exe` 就用 dslab，否则 sigrok，最后回退 mock）。需要固定时加 `--backend`。

包里的 `MCP.cmd` 仍然可用，老配置不需要改；它和执行 `python -m logic_agent.mcp_server` 是同一个入口。

## 手动配置

不运行安装器时，把下面任何一段粘进对应客户端的配置文件即可（`--dry-run` 打印的就是这些内容）。

Claude Desktop / Kiro / Windsurf / Cursor / Cline（`mcpServers`）：

```json
{
  "mcpServers": {
    "logic-analyzer": {
      "command": "D:/tools/logic-agent/runtime/python/python.exe",
      "args": ["-m", "logic_agent.mcp_server", "--data-dir", "D:/tools/logic-agent/data/captures"],
      "env": { "PYTHONDONTWRITEBYTECODE": "1" }
    }
  }
}
```

GitHub Copilot CLI 用同一个 `mcpServers` 结构，但要多两个字段（安装器会自动写）：

```json
"logic-analyzer": {
  "type": "local",
  "command": "D:/tools/logic-agent/runtime/python/python.exe",
  "args": ["-m", "logic_agent.mcp_server", "--data-dir", "D:/tools/logic-agent/data/captures"],
  "tools": ["*"]
}
```

VS Code（`servers`，必须有 `type`）：

```json
{
  "servers": {
    "logic-analyzer": {
      "type": "stdio",
      "command": "D:/tools/logic-agent/runtime/python/python.exe",
      "args": ["-m", "logic_agent.mcp_server", "--data-dir", "D:/tools/logic-agent/data/captures"]
    }
  }
}
```

Codex（`~/.codex/config.toml`）：

```toml
[mcp_servers.logic_analyzer]
command = "D:\\tools\\logic-agent\\runtime\\python\\python.exe"
args = ["-m", "logic_agent.mcp_server", "--data-dir", "D:\\tools\\logic-agent\\data\\captures"]
env = { PYTHONDONTWRITEBYTECODE = "1" }
```

等价的 CLI 写法（客户端自己会写到正确位置）：

```powershell
codex mcp add logic_analyzer -- D:\tools\logic-agent\runtime\python\python.exe -m logic_agent.mcp_server
claude mcp add -s user logic-analyzer -- D:\tools\logic-agent\runtime\python\python.exe -m logic_agent.mcp_server
code --add-mcp '{"name":"logic-analyzer","type":"stdio","command":"D:/tools/logic-agent/runtime/python/python.exe","args":["-m","logic_agent.mcp_server"]}'
```

## 接上之后

1. 重启客户端（Claude Desktop 要连托盘图标一起退出），让配置生效。
2. 让 Agent 先调 `capabilities`、`list_devices`、`self_test`，再采集；推荐流程见 [`../README.md`](../README.md)。
3. 设备同时只能被一个进程占用：DSView、PulseView、`CHECK.cmd` 和 MCP 服务不要同时开。

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 客户端里看不到 `logic-analyzer` | 配置写在别的文件里，或客户端没重启 | `logic-agent mcp-install --list` 确认路径；完全退出后重开 |
| 服务启动后立刻退出 | 包被移动/删除，命令里的绝对路径失效 | 重新运行 `INSTALL-MCP.cmd` |
| `dslab=no` | 包内 `runtime\bin\dslab.exe` 缺失 | 重新解压完整包，运行 `VERIFY-FILES.cmd` |
| 采集报 `access denied` | 数据目录只读（例如放在 `Program Files`） | 换到用户可写目录，或用 `--data-dir` 指过去 |
| 设备被占用 / `Demo Device` | DSView 或另一个采集进程在用 USB | 关掉其它进程，串行重试；见 `docs\TROUBLESHOOTING.md` |
| 配置被写坏 | 安装器遇到无法解析的文件会报错并跳过 | 按报错修好该文件的 JSON/TOML，再重新运行 |
