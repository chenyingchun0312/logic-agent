# 上游来源与同步说明

本文回答两个维护问题：哪些代码是自研的，哪些来自上游；DSView 或 libsigrokdecode 升级后怎么做。

## 1. 代码与资源归属

| 部分 | 位置 | 来源/性质 |
|---|---|---|
| Python Agent、CLI、MCP server、归档/分析/解码编排 | `logic_agent/` | 本项目，MIT |
| `dslab` CLI、JSON 输出、设备枚举、采集、固件校验和安装 | `dslab/` | 本项目，GPL-3.0+ |
| DSLogic 采集驱动核心 | `.deps/dsview/libsigrok4DSL/` | DSView v1.3.2，`bootstrap.ps1` 拉取后静态链接 |
| 对 DSView 的本地适配 | `patches/dsview-v1.3.2-dslab.patch` | 只改 `libsigrok4DSL/hardware/DSL/dslogic.c` |
| 通用协议解码器 | `.deps/sigrok/decoders/` | 上游 libsigrokdecode，随 sigrok-cli 拉取 |
| Smart QSPI 解码器 | `decoders_ext/smart_qspi/` | 从 DSView v1.3.2 移植并适配，GPL-2.0+ |
| FPGA bitstream、`.dsc` | `.deps/dsview/DSView/res/` | DreamSourceLab，`res/license.txt`（MIT 风格） |
| FX3 固件 `DSLogicPlus.fw` | `.deps/firmware/` | DSView 历史提交 `f17c612`，同上许可 |
| WinUSB 驱动 | 打包机 DSView 安装目录 | 厂商签名驱动，可选打包 |

所有版本和哈希固定在 `deps.json`，组件许可证汇总见 `THIRD_PARTY.md`。

`dslab.exe` 不是 DSView GUI 的复制品，而是本项目的 C CLI，静态链接 DSView 的 `libsigrok4DSL`：

```text
logic_agent（本项目） -> dslab.exe（本项目 CLI + 上游 libsigrok4DSL） -> DSLogic Plus USB
```

USB/FX3/FPGA 时序来自上游；命令行、JSON 契约、Agent 工具边界、归档和限额来自本项目。

## 2. 当前基线

| 组件 | 基线 | 定义位置 |
|---|---|---|
| DSView | `v1.3.2` / `03131dd261f08f84262c0172fd051b16cc3bb586` | `deps.json` → `dsview` |
| sigrok-cli / libsigrokdecode | `0.8.0-git-f44dd91` | `deps.json` → `sigrok_cli` |
| 嵌入式 Python | `3.13.17` | `deps.json` → `python_embed` |
| 固件 | `90bb9e55…`，8120 字节 | `deps.json` → `firmware` |

> 2026-10-30 核对：DSView 最新正式 tag 仍为 `v1.3.2`。`origin/master` 可能领先 tag，升级前按第 5 节评估。

确认本地基线：

```powershell
git -C .deps/dsview rev-parse HEAD
git -C .deps/dsview diff --stat      # 只应看到 dslogic.c
```

## 3. 我们对 DSView 改了什么

只改 `libsigrok4DSL/hardware/DSL/dslogic.c`，共 4 类，都标有 `[dslab patch]` 或注释：

1. **扫描阶段禁止自动刷固件。** 官方 `scan()` 会把 `2A0E:0001` 当成老款 DSLogic 并自动上传 `DSLogic33.bin`。本项目改为拒绝隐式写设备，固件写入只走 `dslab firmware install` 的型号和 PID 校验路径。
2. **修复 Windows 下 `usbfd` 源移除逻辑。** libusb 使用 `fd=-1` 哨兵源时，原循环不会移除它。
3. **避免 `receive_data()` 空转占满 CPU。** `fd < 0` 时短暂休眠。
4. **兼容 libusb ≥ 1.0.24 的 Windows 后端。** `libusb_get_pollfds()` 可能返回 `NULL`，原代码会解引用空指针；现在回退到 `fd=-1` 的源继续驱动事件循环。

这些改动必须保留：第 1 条是固件安全边界，第 2、4 条修复 Windows + 新版 libusb 下的扫描/采集失败和崩溃，第 3 条避免 CPU 占满。上游自行修复后才能删除对应补丁块，并且要用真机 `CHECK.cmd` 和 `SELF-TEST.cmd` 证明。

`bootstrap.ps1` 每次都会检查补丁是否已正确应用（`git apply --reverse --check`），所以 `.deps/dsview` 被手工改坏时会自动重新拉取。

### 修改补丁

```powershell
# 在 .deps/dsview 里改代码，然后：
git -C .deps/dsview diff > patches/dsview-v1.3.2-dslab.patch
pwsh scripts/build.ps1
pwsh scripts/bootstrap.ps1 -Force -SkipMsys2 -SkipSigrok -SkipPythonEmbed -SkipVenv   # 验证补丁能干净应用
```

## 4. Smart QSPI 的复用边界

`smart_qspi` 来自 DSView v1.3.2 的 `decoders/qspi/pd.py`，适配记录在 `decoders_ext/smart_qspi/PORTING.md`：

- 目录名改为解码器 id `smart_qspi`；
- annotation id 适配上游运行时；
- 用边沿变化跟踪替换 DSView 专有的 `self.matched` 判断；
- 显式限定时钟/CS 边沿，并尊重 `cs_polarity`。

不要整目录复制 DSView 的 `decoders/`：两套解码器 API 不完全兼容。只有经过适配和测试的解码器才放进 `decoders_ext/`。`scripts/test.ps1` 和 `release.ps1` 会自动把 `decoders_ext/` 覆盖到上游解码器目录。

## 5. DSView 升级步骤

1. 查看上游变化：

   ```powershell
   git -C .deps/dsview fetch origin --tags
   git -C .deps/dsview log --oneline --decorate v1.3.2..origin/master
   git -C .deps/dsview diff --stat v1.3.2..origin/master -- libsigrok4DSL
   ```

   重点看 `dslogic.c` 的 `scan()`、`dev_acquisition_start()`、`remove_sources()`、`receive_data()`；`dsl.c`/`dsl.h` 的设备 profile 和采样格式；`lib_main.c` 和 `include/` 的 API；`DSView/res/` 的资源和固件名。

2. 在新基线上重放补丁：

   ```powershell
   git -C .deps/dsview checkout --detach <new-commit>
   git -C .deps/dsview apply --3way ../../patches/dsview-v1.3.2-dslab.patch
   ```

   有冲突时按第 3 节的 4 类语义手工合并，不要机械接受旧代码。

3. 更新 `deps.json` 的 `dsview.commit`/`tag`，必要时重命名补丁文件并更新 `deps.json.dsview.patch`；如有 API 变化，同步 `dslab/src/*.c`。

4. 回归，严格串行：

   ```powershell
   pwsh scripts/build.ps1 -Clean
   pwsh scripts/test.ps1 -RequireDecoders
   pwsh scripts/release.ps1
   & .\dist\stage\logic-agent\CHECK.cmd
   & .\dist\stage\logic-agent\SELF-TEST.cmd
   ```

   再用真实或合成波形验证 `i2c`、`i2s`、`spi`、`qspi` 等常用路径。`CHECK.cmd` 通过不代表协议事务已经出现。

5. 更新本文第 2 节和 `CHANGELOG.md`。

## 6. libsigrokdecode 升级步骤

1. 安装或取得新版 sigrok-cli Windows 运行时；
2. `pwsh scripts/bootstrap.ps1 -SigrokRoot <新目录> -Force -SkipMsys2 -SkipPythonEmbed -SkipVenv`；
3. 更新 `deps.json` 的 `sigrok_cli.version`（以及 `url`/`sha256`，如果使用重新打包的归档）；
4. 检查 `logic_agent/decoders.py` 中各协议的通道角色、选项名和输出事件是否变化；
5. `pwsh scripts/test.ps1 -RequireDecoders`；
6. 同步 `docs/protocol-decoding.md` 和 `capabilities` 输出，重新发布。

新版解码器可能改变注解文本、选项名或事件顺序。不要只看"能加载"，必须看事件是否符合 `tests/test_decoders.py` 的期望。

## 7. 固件与驱动

- DSView 升级后先对比 `DSView/res/*.bin`、`*.fw`、`.dsc` 和 `license.txt`，许可证变化时同步 `THIRD_PARTY.md`。
- 固件写入前必须确认目标设备是 DSLogic Plus，且固件内描述符为 `2A0E:0020`；正常设备不要刷固件。
- 固件变化时更新 `deps.json` 的 `firmware` 条目（URL、大小、SHA-256）。

## 8. 检查表

- [ ] `deps.json` 中的 DSView commit、sigrok 版本、固件哈希已更新
- [ ] 补丁能在新基线上干净应用（`bootstrap.ps1 -Force` 通过）
- [ ] `build.ps1 -Clean`、`test.ps1 -RequireDecoders`、`release.ps1` 通过
- [ ] `CHECK.cmd`、`SELF-TEST.cmd` 真机通过
- [ ] 常用协议在真实或合成波形上有事件
- [ ] `THIRD_PARTY.md`、本文、`CHANGELOG.md` 已更新
