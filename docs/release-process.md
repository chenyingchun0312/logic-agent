# 日常修改与发布

原则：Git 只管源码和固定依赖清单（`deps.json`）；第三方二进制由 `bootstrap.ps1` 拉取，发布包由 `release.ps1` 从当前源码重新组装。任何人在任何机器上，用同一个 commit 应该得到内容相同的包。

## 1. 仓库与镜像

- 主仓库：GitHub（公开）。
- 内部镜像：GitLab，用 GitLab 的 *Repository mirroring → Pull* 从 GitHub 同步，不在镜像上直接提交。
- 如果内网访问 GitHub 慢，可以另外在 GitLab 镜像一份 `DreamSourceLab/DSView`，同事用 `-DsviewRepo` 或 `DSVIEW_GIT_URL` 指过去。

不进入仓库的内容由 `.gitignore` 排除：`.deps/`、`.cache/`、`.venv/`、`build/`、`dslab/build/`、`dist/`、运行输出，以及所有 `*.fw`、`*.bin`。

## 2. 日常修改

```powershell
git switch -c <topic>
# 修改 logic_agent/、dslab/src/、scripts/ 或 packaging/
pwsh scripts/test.ps1 -RequireDecoders
pwsh scripts/build.ps1          # 改了 dslab/ 或 patches/ 时
git diff --check
```

一个提交只做一件逻辑变更，并在 `CHANGELOG.md` 的 *Unreleased* 下记一条。

## 3. 版本号

只在 `logic_agent/__init__.py` 的 `__version__` 里改。`pyproject.toml`、MCP `serverInfo`、`release.ps1` 生成的包名和 `VERSION.txt` 都从这里读。采用 SemVer：

- 修补：bug 修复、文档；
- 次版本：新增工具、协议、后端，兼容旧调用；
- 主版本：MCP 工具参数或输出不兼容。

## 4. 生成发布候选

```powershell
pwsh scripts/bootstrap.ps1      # 依赖已就绪时几秒内跳过
pwsh scripts/build.ps1 -Clean
pwsh scripts/release.ps1
```

`release.ps1` 依次执行：

1. 预检：`dslab.exe`、DSView 资源、固件（校验 `deps.json` 中的哈希）、sigrok-cli、解码器、嵌入式 Python 都存在；
2. 组装 `dist/stage/logic-agent/`；
3. 生成 `checksums.sha256` 并校验；
4. 用包内 `logic-agent.cmd` 跑 `capabilities`；
5. 以包内的 `dslab.exe`、`sigrok-cli` 和解码器跑全部 pytest，解码器测试被跳过视为失败；
6. 用 Windows PowerShell 5.1 跑 `CHECK -SkipHardware`（与同事的实际入口一致），其中包含在临时 home 里执行一次 `mcp-install`，确认生成的 MCP 配置指向包内解释器；
7. 清理缓存，重算并复验校验和；
8. 压缩为 `dist/logic-agent-dslogic-plus-v<版本>-win-x64.zip`，生成 `.sha256` 和 `.json`；
9. 解压到新目录再校验一次。

驱动：打包机装了 DSView 时自动带上 `C:\Program Files\DSView\Drivers`，也可以用 `-DriverDir`、`-Zadig` 指定，或用 `-NoDriver` 什么都不带。**GitHub 公开 Release 用 `-NoDsviewDriver -Zadig <zadig.exe>`**：DreamSourceLab 驱动包的再分发条款不明确，只随内部包分发。工作区有未提交改动时，`VERSION.txt` 的 commit 会带 `-dirty` 后缀，这样的包不能对外发布。

## 5. 硬件门禁（手动）

接上 DSLogic Plus，关闭 DSView，**串行**运行：

```powershell
& .\dist\stage\logic-agent\CHECK.cmd
& .\dist\stage\logic-agent\SELF-TEST.cmd
```

两者都必须输出顶层 `"ok": true`。需要协议结论时，再用已知波形或实际板卡验证常用协议。

## 6. 发布

```powershell
git tag -a v1.2.1 -m "logic-agent 1.2.1"
git push origin v1.2.1
```

在 GitHub 上为该 tag 建 Release，上传 `dist/` 下的 zip、`.sha256`、`.json`，发布说明里写明：

- `CHANGELOG.md` 中该版本的内容；
- DSView commit 和固件哈希（见包内 `VERSION.txt`）；
- GPL 组件的对应源码：本仓库该 tag，加上 `deps.json` 中的 DSView 提交和 `patches/`；
- 硬件门禁结果和已知限制（例如 CAN CRC 未校验）。

## 7. npm 包

同一份 `dist/stage/logic-agent` 还发到 npm（`logic-agent-mcp`）。tag 推上去之后
`.github/workflows/npm-publish.yml` 会跑完整门禁并自动发布，正常发版不用手工做；手工流程：

```powershell
pwsh scripts/pack-npm.ps1 -SelfTest
npm publish dist/npm/logic-agent-mcp-<版本>.tgz --access public
```

前提是 `deps.json` 的 `sigrok_cli.url`/`sha256` 已固定（一次性操作，见 [`npm.md`](npm.md)）；
没固定时 workflow 会在第一步失败并打印修复命令。npm 版本号不能复用：发错了发下一个补丁版本。

## 8. 回滚

不要删除或覆盖已发布的 Release。发现严重问题时，把上一个版本标记为推荐版本，在 Git 上 `revert` 对应提交并发布新的补丁版本，不要强推历史。

## 9. 常见坑

- 不要提交 `dist/`：zip 每次重打哈希都不同，仓库会迅速膨胀。
- 不要只替换 `dslab.exe`：DLL、`res/`、文档要一起来自同一次 `release.ps1`。
- 不要并行跑 `CHECK.cmd` 和 `SELF-TEST.cmd`：它们争用同一个 USB 设备。
- 只在 `stage/` 里测过不算数，以解压后的校验结果为准（脚本已自动做）。
