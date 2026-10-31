# USB 驱动安装

## 需要什么

DSLogic Plus 在 Windows 上需要 **WinUSB** 驱动，设备 ID 为：

```text
USB\VID_2A0E&PID_0020
```

不需要为了采集而安装完整 DSView；Python 和解码器已随本包提供，驱动视打包情况而定（见下文）。

## 方法 A：本包一键安装

1. 关闭 DSView、PulseView 和其他可能占用设备的软件。
2. 双击 `INSTALL-DRIVER.cmd`，允许 UAC。
3. 运行 `CHECK.cmd`。

包里有 `driver\DSView-Drivers\` 时，脚本调用 DreamSourceLab 的 WinUSB 驱动安装程序 `dpinst-amd64.exe`。GitHub 公开发布的包不带这个驱动包，脚本会自动打开随包的 Zadig，按方法 B 操作即可。

## 方法 B：Zadig

如果方法 A 失败：

```powershell
INSTALL-DRIVER.cmd -OpenZadig
```

在 Zadig 中勾选 `Options → List All Devices`，选择 USB ID 为 `2A0E 0020` 的 DSLogic 设备，驱动选 `WinUSB`，点击 `Install Driver`。不要选择无关的 USB 设备。

## 方法 C：官方 DSView

也可以安装 DSView v1.3.2，让它的安装程序部署同一套驱动。安装完成后仍可直接使用本包，二者不要同时打开设备。

## 如何确认

```powershell
FIRMWARE-STATUS.cmd
CHECK.cmd
```

正常结果应包含：

```json
{"ok":true,"plus_present":true,"bootloader_present":false}
```

若显示 `bootloader_present:true` 或 `plus_present:false`，先看 `docs\FIRMWARE.md`，不要反复插拔或盲目刷写。

## 注意

- 通过 Hub 连接可能导致枚举或采样不稳定，优先直连。
- 若设备管理器显示黄色感叹号，先关闭占用进程，再安装 WinUSB。
- 驱动来自 DreamSourceLab DSView 安装包，只在打包机装有 DSView 时才放进本包。包里没有 `driver\DSView-Drivers` 时，请用方法 B 或方法 C。
