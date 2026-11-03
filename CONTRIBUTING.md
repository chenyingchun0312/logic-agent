# 参与贡献

欢迎提交 issue 和 PR。项目约定、目录职责和"完成标准"见 [`AGENTS.md`](AGENTS.md)，人和 AI 代理共用这一份。

## 环境

- Windows 10/11 x64
- [PowerShell 7](https://github.com/PowerShell/PowerShell/releases)（`pwsh`）
- Git
- Python ≥ 3.11（只用来建 `.venv`，发布包自带 Python）
- 可选：MSYS2。没有的话 `bootstrap.ps1` 会自动装到 `.deps\msys64`

```powershell
git clone <repo>
cd logic-agent
pwsh scripts/bootstrap.ps1
pwsh scripts/build.ps1
pwsh scripts/test.ps1 -RequireDecoders
```

### 网络受限时

| 依赖 | 换源方式 |
|---|---|
| DSView 源码 | `-DsviewRepo <内部镜像 URL 或本地 checkout>`，或设 `DSVIEW_GIT_URL` |
| sigrok-cli | `deps.json` 里的固定 URL（由 `sigrok runtime` workflow 生成；不再从本机拷贝） |
| MSYS2 | `-Msys2Root <目录>`，或设 `MSYS2_ROOT` |
| pip | 设 `PIP_INDEX_URL` |

下载内容都会按 `deps.json` 里的 SHA-256 校验，镜像源也必须给出相同的文件。

## 提交前

```powershell
pwsh scripts/test.ps1 -RequireDecoders
git diff --check
```

- 一个 PR 只做一件事。
- 改了行为就同步文档，并在 `CHANGELOG.md` 的 *Unreleased* 下记一条。
- 动到 `dslab/`、`patches/` 或采集路径时，在 PR 里写明有没有在真机上跑过 `CHECK.cmd` 和 `SELF-TEST.cmd`。没有设备也可以提交，维护者会补测。

## 许可证

贡献 `logic_agent/`、`scripts/`、`tests/` 等目录的代码按 MIT 授权；`dslab/`、`patches/` 按 GPL-3.0-or-later；`decoders_ext/` 按 GPL-2.0-or-later。详见 [`LICENSE`](LICENSE) 和 [`THIRD_PARTY.md`](THIRD_PARTY.md)。
