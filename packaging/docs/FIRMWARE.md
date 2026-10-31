# DSLogic Plus 固件说明与恢复

## 先判断是否真的需要恢复

运行：

```powershell
FIRMWARE-STATUS.cmd
```

正常设备应显示：

```json
{"ok":true,"plus_present":true,"bootloader_present":false}
```

此时 **不要刷固件**。固件只在设备无法以 `2A0E:0020` 正常枚举、且以 `2A0E:0001` FX3 bootloader 出现时使用。

## 随包固件

- 文件：`firmware\DSLogicPlus.fw`
- 大小：8,120 字节
- SHA-256：`90bb9e558767e9eec77749bad905c8bfa172613984d15c6bc10d51d06ebd3080`
- 内嵌 USB 目标：`VID 2A0E / PID 0020`

这是 DSLogic Plus 的 FX3 固件副本，不是 FPGA bitstream。`runtime\bin\res\` 中另有采集所需的 `DSLogicPlus.bin` 等资源。

## 安全恢复流程

1. 关闭 DSView 和其他采集软件。
2. 运行 `FIRMWARE-RECOVER.cmd`。
3. 若设备正常，脚本会直接退出，不会写设备。
4. 若确认为 bootloader，脚本只打印人工命令，不会自动上传。
5. 确认设备是 DSLogic Plus 后，自行执行打印出的命令：

```powershell
runtime\bin\dslab.exe firmware install --file firmware\DSLogicPlus.fw --pid 0020 --json
```

6. 如果返回 `uploaded=true` 但 `reenumerated=false`，拔插一次 USB，再运行 `FIRMWARE-STATUS.cmd`。

7. 回到 `2A0E:0020` 后运行 `CHECK.cmd`。

## 不要做什么

- 不要因为“红灯”就刷固件。DSView 未启动时红灯通常是待机指示。
- 不要把老款 `DSLogic.fw` 或别的型号固件刷到 Plus。
- 不要在设备已正常工作时重复写固件。
- 不要在未确认 USB 目标和驱动时操作 bootloader。

## 来源与许可

固件来自 DreamSourceLab DSView 仓库历史提交 `f17c612` 的 `DSView/res/DSLogicPlus.fw`，按同目录的 DreamSourceLab 许可证（`firmware\LICENSE-DreamSourceLab.txt`，MIT 风格）随包分发。它不属于本项目代码的 MIT/GPL 许可范围。
