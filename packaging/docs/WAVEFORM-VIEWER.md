# 波形查看器

本包产生的两种波形文件用途不同：

| 文件 | 格式 | 推荐打开方式 |
|---|---|---|
| `capture.vcd` | 标准 Value Change Dump | 双击，或 PulseView / GTKWave / Surfer |
| `capture.raw` | DSLogic cross 原始格式，64 样本一帧、每通道 8 字节、LSB-first | 用本包 `VIEW.cmd`，或用 Agent 读取 |

`.raw` **不是**通用波形格式，不能直接拖进 PulseView。需要看图时，优先打开同目录的 `capture.vcd`；没有 VCD 时，用归档的 `capture_id` 导出：

```powershell
VIEW.cmd 20261030T090000Z-1a2b3c4d
```

它会从 `.logic_agent_data\captures\<capture_id>` 重建标准 VCD 并打开查看器。

## PulseView 启动后没有窗口

旧版 PulseView 启动时会扫描 USB，可能卡住。打开文件时加 `-D -c` 跳过设备扫描：

```text
pulseview.exe -D -c "文件.vcd"
```

## 安装查看器

`VIEW.cmd` 依赖的运行时已随包提供，不需要另装 Python。若同事也要图形界面：

1. 安装 PulseView：
   ```powershell
   winget install --id Sigrok.PulseView --exact --accept-package-agreements --accept-source-agreements
   ```
2. 如首次启动报缺少 `MSVCR100.dll`：
   ```powershell
   winget install --id Microsoft.VCRedist.2010.x64 --exact --accept-package-agreements --accept-source-agreements
   ```
3. 新建快捷方式，目标填：
   ```text
   "C:\Program Files (x86)\sigrok\PulseView\pulseview.exe" -D -c
   ```
4. 右键任意 `.vcd` → 打开方式 → 选择 PulseView，并勾选“始终使用”。

也可改用 GTKWave 或 Surfer；它们对 VCD 支持更直接，但不属于本包依赖，需自行安装。

## 采集边界

USB 是独占资源。查看器只打开 `.vcd` 文件时加了 `-D`，不会扫描或占用设备；但不要同时让 DSView、PulseView 采集和本工具采集同一个设备。
