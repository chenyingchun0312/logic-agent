# DSView / DSLogic Plus + AI Agent

## 两个地址是什么关系

- https://github.com/DreamSourceLab/DSView 是 DSView 的官方源码仓库，tag `v1.3.2` 对应本次使用的上游提交
  `03131dd261f08f84262c0172fd051b16cc3bb586`。
- https://dreamsourcelab.cn/download/ 是厂商的发行版下载页。Windows 的 `DSView_v1.3.2_x64_setup.exe`
  是官方编译、签名/打包的安装包，不是该页面的“源码包”。

因此安装包本身不等于开源发布物，但其中的 GUI 与 `libsigrok4DSL` 源码在 GitHub 上以 GPL 提供。
FPGA bitstream（`*.bin`）和 FX3 固件（`*.fw`）不属于 GPL 源码。它们在 DSView 仓库的
`DSView/res/` 下，按同目录的 `license.txt`（DreamSourceLab，MIT 风格）授权；本项目的发布包按该许可
附带它们和 `license.txt`，见 `THIRD_PARTY.md`。

## 结论：怎么接入 AI

不要重写 GUI，也不要 fork 整棵 DSView。本仓库采用：

```
AI / MCP
  -> logic_agent (Python: 归档、统计、UART、边界检查)
  -> dslab.exe (薄 CLI，静态链接官方 libsigrok4DSL)
  -> DSLogic Plus (USB)
```

这样上游升级时只需更新 `deps.json` 中的 DSView 提交并重放 `patches/`；Agent 只依赖稳定的 JSON 契约。

## DSLogic Plus 固件与 USB 身份

正常设备：

- `VID:PID = 2A0E:0020`
- 产品名：`DSLogic PLus`（上游拼写如此）
- 驱动：WinUSB（Zadig 或 DSView 自带驱动）

FX3 bootloader：

- `VID:PID = 2A0E:0001`
- 只有 1 个接口、2 个端点，且没有产品字符串

**关键坑**：官方 `dslogic.c` 的 `scan()` 按 PID 选 profile，`0001` 同时是老款 DSLogic 的 PID。
因此 bootloader 会被误认成老款 DSLogic，并自动上传 `DSLogic33.bin`。本仓库的
`dslab firmware install` 不复用这条路径：它先解析固件内嵌的 USB 设备描述符，要求目标严格为
`2A0E:0020`，否则拒绝写入。

### 固件文件从哪里来

官方 v1.3.2 安装包和当前源码树都**没有** `DSLogicPlus.fw`，只有 `DSLogicPlus.bin`（FPGA）。
真正的 Plus FX3 镜像在官方历史提交
`f17c612cb1d600a50ca4cdf804967770364b4f08` 的 `DSView/res/DSLogicPlus.fw`：

- 8,120 字节
- SHA-256 `90bb9e558767e9eec77749bad905c8bfa172613984d15c6bc10d51d06ebd3080`
- 内嵌描述符 `VID 2A0E / PID 0020`

本仓库不提交该二进制；`scripts/bootstrap.ps1` 按 `deps.json` 固定的 URL 和 SHA-256 把它下载到 `.deps/firmware/DSLogicPlus.fw`。

### 恢复流程

```powershell
# 1. 关闭 DSView，避免占用 USB
# 2. 查看真实 USB 状态（读固件内描述符，不写设备）
dslab firmware status --json

# 3. 安装；会校验固件 PID，并校验设备确为 FX3 bootloader
dslab firmware install --file .deps\firmware\DSLogicPlus.fw --json

# 4. 若命令返回 uploaded=true 但 reenumerated=false，物理拔插一次 USB
# 5. 确认已回到 2A0E:0020
dslab firmware status --json
dslab doctor --json
```

如果 `0020` 在设备管理器里显示为未知设备，用 Zadig 对它安装 WinUSB。

## 常用命令

```powershell
dslab doctor --json
dslab devices --json
dslab capabilities --json
dslab capture once --channels D0 --rate 1M --duration 10ms --json
dslab capture once --channels D0,D1 --rate 10M --trigger "D0=R" --trigger-pos 10 --json
```

`--json` 模式只向 stdout 输出一条 JSON；驱动日志默认关闭，排障时加 `--verbose`。

采集目录包含：

- `capture.raw`：DSLogic cross 格式，64 个样本为一帧，每帧按启用通道顺序各 8 字节，LSB-first
- `capture.vcd`：标准 VCD
- `meta.json`：采样率、样本数、触发、耗时等

## Python / MCP

```powershell
python -m logic_agent.cli devices
python -m logic_agent.cli capture --channels D0 --sample-rate 1000000 --duration 0.01
python -m logic_agent.mcp_server
```

`DslabBackend` 调用 `dslab` 并把 raw 拆成每通道 0/1 字节，交给原有的归档、窗口分析和 UART 解码。
后端优先级：`dslab` → `sigrok` → `mock`。可用 `--backend dslab` 显式指定。

## 长期维护

1. 固定上游 commit；升级时先跑 `tests/test_dslab.py` 与真机短采集。
2. 固件从 `--file`、`DSLAB_FIRMWARE` 或 DSView 安装目录读取；源码仓库不提交固件，发布包在 `firmware/` 下附带。
3. 新增硬件型号时，先补 profile 与固件 PID 校验，再允许上传。
4. Agent 侧只依赖 CLI JSON；不要把 raw 数组塞进 MCP 工具结果。

## 交付关系补充

下载页给的是**厂商发行版**，GitHub 给的是**对应源码**；两者不是同一个交付物。
`DSView_v1.3.2_x64_setup.exe` 是官方编译与打包的 Windows 安装包，不能因为下载页没有
“源码”字样就认为它闭源；同样也不能因为源码是 GPL 就认为安装包内所有二进制资源都可再分发。

**不建议**直接在官方 v1.3.2 上给 GUI 加一个 CLI 开关：DSView 是 Qt GUI 程序，采集会话
与窗口、配置持久化强耦合，把 Agent 接进 GUI 会变成难维护的进程自动化。独立薄 CLI 才是
长期边界。社区 fork 可以参考其 `dslab` 与空转泵思路，但它们各自
携带 vendor 快照，上游升级时仍需人工合并。

## 硬件灯与 USB 重启

官方中文手册 `ug25.pdf` 第 13 页说明：DSView 未运行时 DSLogic 指示灯为红色是正常待机状态；
DSView 识别设备后才变绿。红灯不是固件损坏码，也不是 USB 未枚举。

先核对枚举，再判断软件：

``powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_2A0E' } |
  Select-Object Status,Class,FriendlyName,InstanceId,Problem
dslab firmware status --json
dslab doctor --json
``

正常应为 `2A0E:0020`、`Status=OK`、`Problem=CM_PROB_NONE`。设备曾经过外接 Hub
（历史枚举出现 `VID_2109&PID_2822`）时，长期维护建议直插主机后置 USB 口并使用短数据线；
Hub 供电压降常表现为采集崩溃或掉线，而不是清晰的“线坏了”。

本机就曾在 `DSView` 目录下丢失 `DSLogicPlus.fw`，安装包只装了 FPGA 的
`DSLogicPlus.bin`；`dslab` 的 `firmware status` 因此能发现“设备正常枚举但资源缺失”的情况。
恢复时只接受内嵌描述符为 `2A0E:0020` 的镜像，不要直接把旧款 `DSLogic.fw` 刷到 Plus。

## 无接线自检（推荐每次升级后先跑）

``powershell
pwsh scripts/selftest-dslogic-plus.ps1
python -m logic_agent.cli self-test
``

它启用 DSLogic 自带的内部测试图案，经过真实 USB、FPGA、采样与落盘路径，检查 raw 是否为
稳定的 `0xAA/0x55` 交替流，并要求产生跳变。本机实测：100 MHz、4096 样本、512 字节 raw、
4095 次跳变、约 48 KB VCD。

这项自检证明**采集链路本身正确**，不证明外部 D0 接线或协议正确。外部信号仍建议用已知的
3.3 V 方波/PWM，并与分析仪共地。

## 已修复的上游/适配问题（维护时勿回退）

1. `dslab` 的 `set_u64/set_i16/set_bool/set_vth` 曾对传给 `ds_set_actived_device_config()`
   的 `GVariant` 再次 `g_variant_unref()`。库内 `sr_config_set()` 已经 sink 并在返回前 unref，
   调用方再 unref 会造成浮点引用误用/堆损坏。修复后不再崩溃。
2. 采集前必须显式设置 `SR_CONF_DEVICE_MODE = LOGIC`；否则设备会话可能沿用别的模式。
3. Windows + libusb ≥ 1.0.24 的 `libusb_get_pollfds()` 返回 NULL；已改为注册 `fd=-1`
   空转事件源，并在 `receive_data()` 中 `g_usleep(500)` 限速。`remove_sources()` 需要显式
   注销 `-1` 源，否则会话无法自然结束。
4. `logic_agent/backends/dslab.py` 曾把可执行文件路径重复拼入 argv，实际命令变成
   `dslab.exe dslab.exe capture once ...`。已修复；同时按通道索引排序，避免 `D3,D0` 请求
   与 raw 块顺序错配。
5. CLI 的 JSON 元数据曾使用配置前的默认采样率；现改为使用请求值做超时计算，并在采集后
   回读设备实际值用于 VCD 与 JSON。内部测试模式会强制 100 MHz，回读能避免谎报。

## 一次完整的嵌入式闭环

1. `self_test`：确认采集链健康。
2. 接好目标板：D0…Dn、共地、电压在设备量程内（Plus 逻辑阈值可调，但不要接 12/24 V）。
3. `capture` 短窗口，先拿统计；必要时 `analyze_window` 缩小到边沿附近。
4. 对 UART 用 `decode_uart`，对时钟/PWM 用 `measure_period`；结论必须引用具体窗口。
5. 固件/驱动更新后重复 1–4，并保存 `runs/` 下的 raw、VCD、meta 作为回归证据。
