# 安装与首次验收

## 适用环境

- Windows x64（Windows 10/11）。
- DSLogic Plus，正常模式 USB ID 为 `2A0E:0020`。
- 直连 USB 2.0/3.0 端口，使用短数据线；不建议经过 USB Hub。

## 三步安装

1. 解压整个 `logic-agent` 目录到英文路径，例如 `D:\tools\logic-agent`；先运行 `VERIFY-FILES.cmd`，末行应为 `All N files verified.`。
2. 插好设备。若设备管理器里是未知设备，先运行 `INSTALL-DRIVER.cmd`。
3. 双击 `CHECK.cmd`，看到顶层 `"ok": true`。

无需安装 Python。`runtime\python\python.exe`、`sigrok-cli` 和解码器都已随包提供。请先解压，不要从压缩包预览窗口直接运行。

## 四条验收命令

```powershell
VERIFY-FILES.cmd
CHECK.cmd
SELF-TEST.cmd
FIRMWARE-STATUS.cmd
```

- `VERIFY-FILES.cmd`：按 `checksums.sha256` 校验包内全部文件，确认解压完整。
- `CHECK.cmd`：验证软件、全部协议入口、MCP、设备枚举和真实短采集。
- `SELF-TEST.cmd`：使用设备内部测试图案，验证 USB/FPGA/采样/落盘；不需要外接探针。
- `FIRMWARE-STATUS.cmd`：查看设备是否以 `2A0E:0020` 正常运行。

自检输出在 `data\selftest`，短采集输出在 `data\capture-smoke`。这些目录可删除。

## 采集

```powershell
CAPTURE.cmd --channels D0 --rate 1M --samples 10000 --out data\my-capture --json
CAPTURE.cmd --channels D0,D1 --rate 10M --trigger D0=R --trigger-pos 10 --duration 10ms --json
```

`--rate` 是采样率，`--samples` 与 `--duration` 二选一；触发支持例如 `D0=R`、`D0=F`。

输出：

- `capture.raw`：DSLogic cross 格式，64 样本一帧，每帧按启用通道各 8 字节，LSB-first。
- `capture.vcd`：标准 VCD，可直接用波形查看器打开。
- `meta.json`：采样率、样本数、通道、触发、耗时和健康标志。

## AI/MCP

把路径换成实际目录：

```toml
[mcp_servers.logic_analyzer]
command = "cmd"
args = ["/c", "D:/tools/logic-agent/MCP.cmd", "--backend", "dslab", "--data-dir", "D:/tools/logic-agent/data/captures"]
```

Agent 推荐顺序：`capabilities` → `list_devices` → `self_test` → `capture` → `analyze_window` / `decode_protocol`。

## 版本与压缩包校验

版本、源码 commit 和固件哈希见根目录 `VERSION.txt`。压缩包同目录附有同名 `.zip.sha256`，可用 `Get-FileHash <zip> -Algorithm SHA256` 对照。不要混用不同版本的压缩包。

## 注意

- DSView 和本工具不能同时占用 USB。
- 逻辑分析仪输入电压有限，测量 5 V 以上、RS-485/CAN 差分或工业总线前必须加隔离/电平转换。
- 正常设备不要运行固件恢复。固件只在 `2A0E:0001` bootloader 时使用。
