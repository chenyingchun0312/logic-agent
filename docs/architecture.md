# 设计说明

## 目标

让 Agent 能“提问 → 采集 → 观察 → 下钻 → 给结论”，同时保持三件事：

1. **可验证**：结论能追溯到某次 capture 和某个窗口。
2. **可控**：不会因一次工具调用产生不可接受的数据量或硬件占用。
3. **可替换**：换分析仪不重写 Agent 逻辑。

## 分层

### 1. 传输层：MCP

使用 MCP stdio，因为本地硬件工具天然运行在用户机器上，不需要额外网络服务。

选择 JSON-RPC 行分隔消息，保持实现最小、易调试。

### 2. 工具层

工具数量刻意保持少，避免 Agent 在多个近似接口间犹豫：

| 工具 | 作用 | 不做什么 |
|---|---|---|
| `capabilities` | 后端、限额、工作流 | 不采集 |
| `list_devices` | 设备发现 | 不打开设备 |
| `capture` | 采集并归档 | 不返回原始数组 |
| `analyze_window` | 窗口统计与边沿 | 不做协议断言 |
| `decode_uart` | 确定性 UART 解码 | 不猜测未知波特率 |
| `decode_protocol` | 22 类直接/堆叠协议结构化事件 | 不返回逐 bit 原始数组 |
| `measure_period` | 周期/频率/占空比/抖动 | 不对非周期信号给频率 |
| `import_csv` | 导入 DSView 导出数据 | 不修改原始 CSV |
| `list_captures` | 历史检索 | 不分析 |

### 3. 协议解码层

采集与协议解析分离。协议解析调用随包的 `sigrok-cli` 与 libsigrokdecode：

```text
capture archive (.bits)
  -> 按协议角色选择通道窗口
  -> 打包为 sigrok binary
  -> sigrok-cli -P "lower:...,upper:..."
  -> 注解解析为 {type, text, source, layer, value?, start_s, end_s}
```

- 协议注册表是唯一配置源，CLI、MCP schema、`capabilities` 都从它生成。
- 支持单层与解码器堆叠，例如 `spi -> spiflash`、`uart -> modbus`。
- 选项带类型、范围与枚举校验；堆叠重名选项使用 `decoder.option`。
- 只有显式注册的协议才开放给模型，避免参数 schema 与事件语义失控。
- 注册表标注 `verification`：`synthetic-waveform` 或 `contract`，不把可加载
  等同于已证明协议行为。
- 解码器运行时优先从便携目录发现，其次环境变量/`PATH`；不要求系统安装。
- DSView 解码器只有经 `decoders_ext/` 适配并验证后才进入交付包。

### 4. 领域层

`CapturedSignal` 是后端与存储之间的稳定契约：

```python
backend: str
device_id: str
channels: tuple[Channel, ...]
sample_rate_hz: int
samples: dict[str, bytes]   # 每采样 1 字节，值 0/1
```

用 0/1 字节而非位图作为内存表示，换取实现简单和跨后端一致性；落盘时再压缩为 bit pack。

### 5. 存储层

每次采集一个目录，写入采用“临时目录 + 原子 rename”，避免 Agent 看到半成品。

manifest 记录：

- 设备、后端、采样率、采样数、时长
- 触发配置及是否真正生效
- 每通道文件、字节数、SHA-256
- 后端 warning

### 6. 分析层

统计优先于解码：

- 边沿数、上升/下降沿
- 高电平占比
- 最窄/最宽高低脉冲
- 粗略频率

这些指标足以让 Agent 决定“这是时钟 / PWM / 异步串行 / 无信号”，并把后续工具调用收窄到正确通道和窗口。

## 上下文预算

假设 1 MS/s、10 s、8 通道：

- 原始 CSV 约 8000 万字符，不可用。
- 统计摘要约 1 KB。
- 每通道 80 个边沿约数 KB。

因此“先统计、再窗口、再解码”不是优化项，而是可用性的前提。

## 失败模式与处理

| 失败 | 处理 |
|---|---|
| 设备未连接 | `backend_unavailable`，附安装/驱动提示 |
| 通道名错误 | `validation_error`，附可用通道列表 |
| 采样率过低 | 明确最小过采样要求，要求重采 |
| 解码 framing error | 返回 `errors`，不静默修正 |
| 触发未生效 | `trigger_applied=false` + warning |
| 工具结果过大 | 拒绝并要求缩小窗口 |
| 文件损坏 | SHA-256 校验失败，拒绝分析 |

## 后续扩展顺序

1. `SaleaeBackend`：覆盖 Logic 8/Pro。
2. `import` 后端：`.sal` / `.vcd` / CSV，先支持离线分析。
3. 继续扩展已注册协议的波形级验证（JTAG、SWD、1-Wire、SD 卡等），补协议专用断言。
4. 触发编排：`capture_until` 工具，内部多轮短采集，仍返回摘要。
5. 可视化：生成 SVG/PNG 缩略图作为资源，便于人机共同复核。

## 安全边界

- 命令执行只允许固定可执行文件与固定参数结构。
- 所有外部输入先经过 `CaptureRequest` 校验。
- 路径不接受用户拼接，`capture_id` 由服务端生成并严格匹配。
- 默认禁止超长采集，避免占满磁盘或长时间占用设备。

