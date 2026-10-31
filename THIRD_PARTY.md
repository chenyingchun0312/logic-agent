# Third-party components and licenses

本仓库只提交源码。下表中的组件由 `scripts/bootstrap.ps1` 按 `deps.json` 固定的版本和 SHA-256 拉取到 `.deps/`，再由 `scripts/release.ps1` 打进便携包。

## 本仓库的许可证划分

| 路径 | 许可证 | 说明 |
|---|---|---|
| `logic_agent/`、`tests/`、`scripts/`、`packaging/`、`docs/` | MIT（`LICENSE`） | 通过子进程调用 `dslab.exe` 和 `sigrok-cli.exe`，不链接 GPL 代码 |
| `dslab/` | GPL-3.0-or-later（`dslab/COPYING`） | 静态链接 libsigrok4DSL，其中 `dslogic.c` 为 GPL-3.0+ |
| `patches/` | GPL-3.0-or-later | 对 DSView 源码的修改 |
| `decoders_ext/smart_qspi/` | GPL-2.0-or-later | 源自 libsigrokdecode / DSView，见文件头 |

## 构建时拉取的组件

| 组件 | 版本 | 来源 | 许可证 | 进入发布包的位置 |
|---|---|---|---|---|
| DSView / libsigrok4DSL | v1.3.2 `03131dd` | https://github.com/DreamSourceLab/DSView | GPL-3.0-or-later（部分文件 GPL-2.0+） | 静态链接进 `runtime/bin/dslab.exe` |
| DSView `res/`（FPGA bitstream、`.dsc`） | v1.3.2 | 同上 `DSView/res/` | `DSView/res/license.txt`（MIT 风格，DreamSourceLab 2017） | `runtime/bin/res/`，许可证见 `runtime/licenses/DreamSourceLab-res-MIT.txt` |
| `DSLogicPlus.fw`（FX3 固件） | 提交 `f17c612` | 同上 `DSView/res/DSLogicPlus.fw` | 同一提交的 `DSView/res/license.txt` | `firmware/`，附 `LICENSE-DreamSourceLab.txt` |
| glib、libusb、zlib、pcre2、iconv、intl、winpthread | MSYS2 UCRT64 | https://packages.msys2.org | LGPL-2.1+ / BSD / zlib 等 | `runtime/bin/*.dll`（动态链接，可替换） |
| sigrok-cli + libsigrokdecode 解码器 | 0.8.0-git-f44dd91 | https://sigrok.org | GPL-3.0+ / GPL-2.0+ | `runtime/bin/sigrok-cli.exe`、`runtime/decoders/` |
| CPython embeddable | 3.13.17 | https://www.python.org | PSF License | `runtime/python/` |
| Zadig | 打包时提供 | https://zadig.akeo.ie | GPL-3.0 | `driver/zadig.exe` |
| DreamSourceLab WinUSB 驱动 | DSView 安装包自带 | 本机 DSView 安装目录 | 厂商签名驱动，再分发条款未明确 | `driver/DSView-Drivers/`（仅内部包；公开 Release 用 `-NoDsviewDriver` 排除） |

## 对外分发的义务

- 发布包含 GPL 二进制（`dslab.exe`、`sigrok-cli.exe`、解码器）。随包提供 `runtime/licenses/`，并在发布说明中给出对应源码位置：本仓库的 tag 加上 `deps.json` 中的上游提交。
- 动态链接的 LGPL 库以独立 DLL 形式提供，用户可以替换。
- 固件和 bitstream 必须与 DreamSourceLab 的 `license.txt` 一起分发。
- DreamSourceLab 的 WinUSB 驱动包只出现在 DSView 官方安装包里，再分发条款不明确，公开 Release 不带它（`release.ps1 -NoDsviewDriver`），只带 Zadig。用户也可以安装 DSView 来获得驱动。
