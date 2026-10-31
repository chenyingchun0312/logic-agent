# 协议解析：22 类直接与堆叠协议

## 结论

当前方案通过同一个 `decode` / `decode_protocol` 入口暴露 22 个协议名。
`capabilities` 里每个协议的 `verification` 字段说明验证级别：

- `synthetic-waveform`：已用结构合法的合成波形验证出实际事件。
- `contract`：已验证解码器可加载、通道/选项 schema 正确；尚无合成波形事件测试。
- 两者都不等于“在你的板子上已经采到有效事务”。

| 对外名称 | 解码器层 | 典型用途 | 验证级别 |
|---|---|---|---|
| `i2c` | `i2c` | 传感器、EEPROM、PMIC | synthetic-waveform |
| `i2s` | `i2s` | 音频 codec / MCU 音频 | synthetic-waveform |
| `spi` | `spi` | Flash、LCD、传感器 | synthetic-waveform |
| `qspi` | `smart_qspi` | SPI NOR Flash、QSPI Flash | synthetic-waveform |
| `can` | `can` | CAN 2.0 / CAN-FD 帧 | synthetic-waveform |
| `jtag` | `jtag` | TAP 状态与移位数据 | contract |
| `swd` | `swd` | ARM SWD 事务 | contract |
| `onewire_link` | `onewire_link` | 1-Wire 复位/存在/位 | contract |
| `ps2` | `ps2` | PS/2 键鼠帧 | synthetic-waveform |
| `mdio` | `mdio` | 以太网 PHY 管理帧 | synthetic-waveform |
| `ir_nec` | `ir_nec` | NEC 红外遥控 | contract |
| `spdif` | `spdif` | S/PDIF 音频 | contract |
| `usb_signalling` | `usb_signalling` | USB LS/FS 线路层 | contract |
| `cjtag` | `cjtag` | IEEE 1149.7 两线调试 | contract |
| `sdcard_sd` | `sdcard_sd` | SD 原生模式 | contract |
| `spiflash` | `spi` → `spiflash` | SPI NOR Flash/EEPROM | synthetic-waveform |
| `sdcard_spi` | `spi` → `sdcard_spi` | SPI 模式 SD 卡 | contract |
| `modbus` | `uart` → `modbus` | Modbus RTU | synthetic-waveform |
| `lin` | `uart` → `lin` | LIN 总线 | contract |
| `onewire_network` | `onewire_link` → `onewire_network` | 1-Wire ROM/器件寻址 | contract |
| `jtag_stm32` | `jtag` → `jtag_stm32` | STM32 JTAG | contract |
| `jtag_ejtag` | `jtag` → `jtag_ejtag` | MIPS EJTAG | contract |

这里的“官方解码器”分成两种来源：

1. **上游 libsigrokdecode 解码器**（I²C、I²S、SPI 等）直接复用，便携包中整套随包分发。
2. **DSView 的解码器**（如 `smart_qspi`）并不都兼容上游运行时。只有经过适配、且
   在 `decoders_ext/` 中登记的模块才进入交付包；不能整目录复制 DSView `decoders`。

这样长期维护边界很清晰：上游协议跟随 libsigrokdecode，厂商专有协议单独维护适配层。

## 使用方式

CLI：

```powershell
# I2C: 传感器地址 0x50，SCL=D0，SDA=D1
.\logic-agent.cmd decode <capture_id> i2c --channels scl=D0,sda=D1

# I2S: 位时钟、声道选择、串行数据
.\logic-agent.cmd decode <capture_id> i2s --channels sck=D0,ws=D1,sd=D2

# SPI Mode 0
.\logic-agent.cmd decode <capture_id> spi --channels clk=D0,mosi=D1,miso=D2,cs=D3 --options cpol=0,cpha=0

# QSPI（Smart QSPI），先按单线 SPI 采集命令/地址/数据
.\logic-agent.cmd decode <capture_id> qspi --channels clk=D0,io0=D1,cs=D2 --options twolinesmode=spi

# CAN：CAN-RX 接 D0，500 kbit/s，采样点 75%
.\logic-agent.cmd decode <capture_id> can --channels can_rx=D0 --options nominal_bitrate=500000,sample_point=75

# SPI Flash：堆叠在 SPI 之上，只返回 Flash 语义层
.\logic-agent.cmd decode <capture_id> spiflash --channels clk=D0,mosi=D1,miso=D2,cs=D3 --options chip=winbond_w25q80dv

# Modbus RTU：堆叠在 UART 上
.\logic-agent.cmd decode <capture_id> modbus --channels tx=D0 --options baudrate=115200
```

MCP 工具：

```json
{
  "name": "decode_protocol",
  "arguments": {
    "capture_id": "20261029T145635Z-7d2aa3f4",
    "protocol": "qspi",
    "channels": {"clk": "D0", "io0": "D1", "cs": "D2"},
    "options": {"twolinesmode": "spi"},
    "max_events": 200
  }
}
```

`capabilities` 会返回当前运行时的位置、支持的协议、通道角色与选项枚举。Agent
应先读取它，不要猜通道映射。

## 通道与选项

### I²C

- 必需：`scl`、`sda`
- 选项：`address_format=shifted|unshifted`
- 注意：默认 `shifted` 显示的是 7 位地址（例如写地址 `0xA0` 显示为 `50`）。

### I²S

- 必需：`sck`、`ws`、`sd`
- 上游 `i2s` 解码器本身没有可调选项；WS 极性等由实际接线和采集窗口决定。
- 事件通常是 `left` / `right`，文本末尾是 8 位十六进制采样值。

### SPI

- 必需：`clk`
- 可选：`mosi`、`miso`、`cs`（至少提供 mosi 或 miso 之一）
- 选项：`cs_polarity`、`cpol`、`cpha`、`bitorder`、`wordsize`
- 建议先按波形确认 CPOL/CPHA；错一相会导致整体错位一个 bit。

### Smart QSPI

- 必需：`clk`、`io0`
- 可选：`io1`、`io2`、`io3`、`cs`
- 选项：`cs_polarity`、`cpol`、`cpha`、`bitorder`、`ads`、`twolinesmode`、`invalidlevel`
- `twolinesmode=spi` 表示先用单线方式解析命令和地址；总线随后切到 Dual/Quad
  时，解码器会按命令表继续处理。
- 采样 Flash 时，命令、地址和数据必须处于同一次 CS 有效期间；不要在字节之间
  人为抬高 CS，否则会被拆成多个独立事务。

### 堆叠协议

`sigrok-cli` 支持用逗号连接多层解码器，例如：

```text
-P "spi:clk=0:mosi=1:miso=2:cs=3,spiflash:chip=winbond_w25q80dv"
```

本工具已封装该语法。默认只返回最上层事件；需要同时查看 SPI/UART 等下层时加
`--include-lower-layers`，或在 MCP 中传 `include_lower_layers: true`。

- 堆叠层的通道角色由最底层定义，例如 `spiflash` 仍使用 `clk/mosi/miso/cs`。
- 选项重名时用 `解码器.选项` 消歧，例如 `spi.cpol`、`uart.baudrate`。
- 普通选项名在唯一时可直接使用，例如 `baudrate`、`chip`、`nominal_bitrate`。
- 每层字段名以 `capabilities.protocols.<protocol>.layers` 为准。

## 事件格式

工具不会把原始波形数组送给模型，只返回有界结构化事件：

```json
{
  "type": "data-write",
  "text": "Data write: 4B",
  "value": 75,
  "start_s": 0.000034,
  "end_s": 0.000058
}
```

- `type` 来自解码器注解类。
- `text` 是解码器的人类可读注解；有 `value` 时以数值为准。
- `source` 是 `sigrok-cli` 的实例名（如 `spiflash-1`）；`layer` 去掉实例编号，
  便于区分堆叠层。
- 默认过滤逐 bit 事件；只有 `include_bits=true` 才返回，避免上下文爆炸。
- `max_events` 上限 5000，默认 500；超过时返回 `truncated=true` 和
  `warnings`，不会静默丢弃。

## 采样率建议

| 协议 | 建议采样率 |
|---|---|
| I²C 100 kHz | ≥ 1 MS/s |
| I²S BCLK 1.5 MHz | ≥ 10 MS/s |
| SPI 1–10 MHz | ≥ 10–50 MS/s |
| QSPI 10–50 MHz | ≥ 100 MS/s，或租用设备允许的最大值 |

DSLogic Plus 有 16 通道和内部缓存；优先短窗口、多次采集，而不是一次拉几十秒。

## DSView 解码器的复用策略

- 上游有的协议，优先用上游实现；不要为了“看起来像官方”去复制 DSView 版本。
- DSView 专有且只依赖通用 `srd` API 的解码器，按下列步骤移植：
  1. 复制到 `decoders_ext/<id>/`，目录名必须等于解码器 `id`；
  2. 用 `sigrok-cli --show -P <id>` 验证能否加载；
  3. 再用合成波形验证事件输出；
  4. 记录来源、修改和 GPL 许可证到 `PORTING.md`。
- `smart_qspi` 已经按此流程适配，并修正了 DSView 专有的 `self.matched` 边沿判断。

## 许可与分发

- 上游 libsigrokdecode 解码器为 GPL-2.0-or-later；便携包保留 `COPYING`。
- `smart_qspi` 同样为 GPL-2.0-or-later，来源为 DSView v1.3.2；修改说明随包提供
  在 `licenses/decoders/smart_qspi-PORTING.md`。
- 内部部署可以直接使用；对外分发修改版时，需要同时提供对应源码和许可证。
- `bin/res/DSLogicPlus.fw` 和 FPGA bitstream 是 DreamSourceLab 资源，按其 `license.txt` 随包分发，不属于上述开源代码，见 `THIRD_PARTY.md`。

## 已知边界

- 当前显式暴露 22 个协议；运行时里其余上游解码器仍在包内，但未进入参数 schema，
  不能通过 `decode` 调用。
- 尚未支持任意解码器自动发现：这是有意限制，避免模型把未验收的注解当成协议结论。
- CAN 解码器当前不校验 CRC（上游 `can` 实现中 `is_valid_crc()` 为 TODO）；
  它输出帧结构、ID、DLC、数据、ACK 与 EOF，不能据此断言 CRC 正确。
- `can-fd` 不作为独立协议名暴露：使用 `can` 并设置 `nominal_bitrate` /
  `fast_bitrate`；具体能否解出取决于帧格式与采样率。
- QSPI 适配器以“命令表 + 状态机”解析，不覆盖所有厂商私有命令；未知命令会显示为
  单线数据，需要补充命令表。
- 真实硬件闭环已验证“采集链路 + 解码链路可调用”；具体协议是否出现有效事务，
  仍取决于探针接线、采样率、触发和被测设备是否真的在通信。
