# logic-agent · DSLogic Plus 便携包

> 版本和构建信息见同目录 `VERSION.txt`。  
> 适用硬件：DSLogic Plus（正常模式 `USB\VID_2A0E&PID_0020`），Windows 10/11 x64。

这个目录是 **Windows x64 便携包**：DSLogic Plus 采集、波形分析、协议解码和 AI/MCP 接入都在里面，不需要安装 Python、DSView 或编译工具。

## 先看结论

- **正常设备不需要刷固件。** 设备以 `USB\VID_2A0E&PID_0020` 出现即可直接使用。
- **固件只用来自救。** 只有设备停在 `2A0E:0001` bootloader 时，才按 `docs\FIRMWARE.md` 人工恢复。
- **驱动只需要 WinUSB。** 用本包的 `INSTALL-DRIVER.cmd`（如果包内带了驱动），或 DSView、Zadig 安装。
- USB 是独占资源，DSView 和本工具不要同时打开设备。
- 必须解压整个压缩包再运行，不要在压缩软件的预览窗口里直接运行。

## 目录结构

```text
logic-agent/
  README.md                本文件
  VERSION.txt              版本、源码 commit、上游基线、固件哈希
  THIRD-PARTY-NOTICES.md   第三方组件与许可证
  CHECK.cmd                一键检查软件、解码器、MCP 和硬件采集
  SELF-TEST.cmd            DSLogic 内部测试图案自检，不需要外接探针
  CAPTURE.cmd              短采集入口
  VIEW.cmd                 从已保存采集导出 VCD 并打开波形
  API.cmd                  logic_agent 命令行入口
  MCP.cmd                  MCP stdio 服务入口
  INSTALL-MCP.cmd          一键把 MCP 接入本机 AI 客户端（Codex/Claude/Copilot/…）
  INSTALL-DRIVER.cmd       WinUSB 驱动安装入口
  FIRMWARE-STATUS.cmd      查看设备/固件运行状态
  FIRMWARE-RECOVER.cmd     仅诊断 bootloader，并打印人工恢复命令
  VERIFY-FILES.cmd         校验包内文件是否完整
  checksums.sha256         包内每个文件的 SHA-256
  runtime/                 dslab.exe、sigrok-cli、Python、解码器、许可证
  firmware/                DSLogicPlus.fw 及 DreamSourceLab 许可证
  driver/                  WinUSB 驱动、Zadig（如果打包时提供）
  docs/                    安装、驱动、固件、协议、故障排查
  data/                    自检和采集输出（可删除）
```

## 最短安装步骤

1. 解压到本机英文路径，例如 `D:\tools\logic-agent`，不要放在中文路径或网络共享。
2. 双击 `VERIFY-FILES.cmd`，最后一行应为 `All N files verified.`，没有 `MISSING` 或 `MISMATCH`。
3. 插好 DSLogic Plus，使用直连 USB 口和短数据线；如果 Windows 不认识设备，双击 `INSTALL-DRIVER.cmd` 并允许 UAC。
4. 双击 `CHECK.cmd`，看到顶层 `"ok": true` 即部署成功。
5. 再双击 `SELF-TEST.cmd`，验证 USB、FPGA、采样和落盘链路。
6. 需要 AI 接入时双击 `INSTALL-MCP.cmd`，自动配置并验证本机的 MCP 客户端。

`CHECK.cmd` 依次检查便携运行时、协议入口、离线 I²C 解码、MCP 初始化、设备枚举和真实短采集，不会刷固件。只想先验证软件、不接硬件时：

```powershell
CHECK.cmd -SkipHardware
```

## 采集与查看

```powershell
CAPTURE.cmd --channels D0 --rate 1M --samples 10000 --out data\my-capture --json
```

输出目录包含 `capture.raw`、`capture.vcd` 和 `meta.json`。双击 `capture.vcd` 可以用 PulseView 等查看器打开；`.raw` 是 DSLogic 原始格式，用 `VIEW.cmd <capture_id>` 转成 VCD，或交给 Agent 分析。详见 `docs\WAVEFORM-VIEWER.md`。

## AI / MCP 接入

双击 `INSTALL-MCP.cmd`，一条命令完成：检测本机安装的 Codex、Claude Code、Claude Desktop、GitHub Copilot（VS Code / CLI）、Cursor、Kiro、Windsurf、Cline，把 `logic-analyzer` 合并写进各自配置，并真实启动一次服务验证 `capabilities`。

```powershell
INSTALL-MCP.cmd              # 检测 + 写入 + 验证
INSTALL-MCP.cmd --list       # 只列出客户端和配置路径
INSTALL-MCP.cmd --dry-run    # 只打印可粘贴的 JSON/TOML
INSTALL-MCP.cmd --remove     # 删除这条配置
```

客户端直接 spawn 包内解释器，不经过 `cmd.exe`，不需要任何环境变量：

```text
D:\tools\logic-agent\runtime\python\python.exe -m logic_agent.mcp_server --data-dir D:\tools\logic-agent\data\captures
```

完整的客户端表格、手动配置、排错见 `docs\MCP-CLIENTS.md`。建议先让 Agent 调用 `capabilities`、`list_devices`、`self_test`，再采集。

## 协议

```powershell
API.cmd decode <capture_id> i2c --channels scl=D0,sda=D1
API.cmd decode <capture_id> i2s --channels sck=D0,ws=D1,sd=D2
API.cmd decode <capture_id> spi --channels clk=D0,mosi=D1,miso=D2,cs=D3 --options cpol=0,cpha=0
API.cmd decode <capture_id> qspi --channels clk=D0,io0=D1,cs=D2 --options twolinesmode=spi
API.cmd decode <capture_id> can --channels can_rx=D0 --options nominal_bitrate=500000
API.cmd decode <capture_id> spiflash --channels clk=D0,mosi=D1,miso=D2,cs=D3
API.cmd decode <capture_id> modbus --channels tx=D0 --options baudrate=115200
```

完整矩阵、通道角色、选项和边界见 `docs\PROTOCOLS.md`。协议验证分两级：标为 `synthetic-waveform` 的已用合成波形验证事件；标为 `contract` 的只证明解码器可加载、通道和选项 schema 正确。CAN 解码器不校验 CRC。

## 完整性

- 包内文件：运行 `VERIFY-FILES.cmd`。
- 压缩包：同名 `.zip.sha256`，用 `Get-FileHash <zip> -Algorithm SHA256` 对照。
- 固件哈希：见 `VERSION.txt`。

## 许可证与源码

- logic-agent（`runtime/app/logic_agent`）：MIT。
- `dslab.exe`：GPL-3.0-or-later，静态链接 DreamSourceLab libsigrok4DSL；`sigrok-cli` 和解码器：GPL。对应源码：项目仓库中与 `VERSION.txt` 的 commit 对应的版本，加上其中 `deps.json` 记录的上游提交；本地补丁另附在 `runtime/licenses/source/`。
- FPGA bitstream 和固件：DreamSourceLab，许可证见 `firmware/LICENSE-DreamSourceLab.txt`。
- 完整列表见 `THIRD-PARTY-NOTICES.md` 和 `runtime/licenses/`。

更多说明：`docs\INSTALL.md`、`docs\DRIVER.md`、`docs\FIRMWARE.md`、`docs\PROTOCOLS.md`、`docs\MCP-CLIENTS.md`、`docs\DSLOGIC-PLUS.md`、`docs\UPSTREAM-SYNC.md`、`docs\TROUBLESHOOTING.md`。
