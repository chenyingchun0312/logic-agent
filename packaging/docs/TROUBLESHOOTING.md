# 故障排查

## 一张表定位

| 现象 | 先检查 | 处理 |
|---|---|---|
| `CHECK.cmd` 里 `dslab.exe` 失败 | 目录是否完整 | 重新解压整个 `logic-agent` 目录，不要只复制 exe |
| `doctor` 报 `activated unexpected device "Demo Device"` | 有另一个采集进程占用 USB | 关闭 DSView、PulseView、另一个 `CHECK`/`SELF-TEST`，串行重试 |
| `doctor-json` 为 false | USB 枚举/驱动 | 插直连 USB 口，短数据线，运行 `INSTALL-DRIVER.cmd` |
| `device_count: 0` | 设备是否在 `2A0E:0001` | 运行 `FIRMWARE-STATUS.cmd`，按 `docs\FIRMWARE.md` 处理 |
| 设备可见但采集失败 | DSView 是否占用 | 关闭 DSView/PulseView，重新运行 `CHECK.cmd` |
| 采集全为低电平 | 探针/接地/电压 | 用 `SELF-TEST.cmd` 区分采集链路与外部接线 |
| 协议无事件 | 通道角色、采样率、CS | 先 `analyze_window` 看边沿，再按 `docs\PROTOCOLS.md` 映射 |
| MCP 客户端启动失败 | command/args 路径 | 重新运行 `INSTALL-MCP.cmd`，它写入包内解释器的绝对路径并验证启动；`--dry-run` 可只打印配置 |
| 客户端列表里没有 `logic-analyzer` | 配置写到了别的文件，或客户端没重启 | `INSTALL-MCP.cmd --list` 看实际路径；Claude Desktop 要连托盘图标一起退出 |
| `runtime DLL` 缺失 | 解压不完整/杀软隔离 | 重新解压，检查杀软隔离区 |
| 红灯 | 是否待机 | DSView 未启动时红灯不是故障码；先看 `FIRMWARE-STATUS.cmd` |

## 重要：硬件检查必须串行

USB 设备是独占资源。不要同时运行 `CHECK.cmd`、`SELF-TEST.cmd`、DSView、PulseView 或两个采集进程。并行运行会让底层驱动打开失败，甚至回退到 `Demo Device`；这表示设备被占用，不是“协议验证通过”。

## 分层排查

1. **软件**：`CHECK.cmd -SkipHardware`。
2. **驱动**：`FIRMWARE-STATUS.cmd`。
3. **内部采样**：`SELF-TEST.cmd`。
4. **真实采集**：`CAPTURE.cmd --channels D0 --rate 10k --samples 2000 --out data\smoke --json`。
5. **协议**：确认接线后，用 `API.cmd decode ...`。

## 关键判断

`SELF-TEST.cmd` 通过，只说明 USB、固件、FPGA、采样和落盘正常；它不能证明外部探针接对、电压正确或总线上有协议事务。

`CHECK.cmd` 里有 `mcp-capabilities-22-protocols: true`，说明 MCP 工具 schema 正常，不代表某个真实设备协议已经出现有效帧。

## 安全

- 不测量超过分析仪额定电压的信号。
- 差分总线（CAN、RS-485 等）需要收发器/隔离器，不能直接把差分线当单端逻辑输入。
- 固件写操作只在 bootloader 场景执行，并遵循 `docs\FIRMWARE.md`。
