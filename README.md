# Debian 13 软件源切换

为 **Debian 13（trixie）** 配置华为云和中科大 HTTPS 软件源。选择一家优先，另一家自动备用；支持备份、索引刷新和失败回滚。

固定使用 Debian 13 配置，不检测发行版。适用于 Debian 13 WSL，也可在具备相同 APT 环境的 Debian 13 系统中运行。

## 快速使用

将 [`switch-mirror.py`](./switch-mirror.py) 下载到 Debian 13 中，在文件所在目录运行：

```bash
# 华为云优先，中科大备用
sudo /usr/bin/python3 switch-mirror.py huawei

# 中科大优先，华为云备用
sudo /usr/bin/python3 switch-mirror.py ustc

# 查看当前配置，无需 sudo
/usr/bin/python3 switch-mirror.py status
```

也可以克隆仓库：

```bash
git clone https://github.com/yierxiaokeai/debian13-mirror-switch.git
cd debian13-mirror-switch
sudo /usr/bin/python3 switch-mirror.py huawei
```

换源功能只需 `switch-mirror.py` 一个文件。需要 `/usr/bin/python3`、APT、可用网络、HTTPS 证书及 Debian archive keyring。请在 APT 或 dpkg 操作完成后运行。

## Windows 菜单入口

已安装 PowerShell 7 且 WSL 发行版名称为 `Debian` 时，将 `切换软件源.ps1` 与 `switch-mirror.py` 放在同一目录，运行：

```powershell
pwsh -NoProfile -File "./切换软件源.ps1"
```

输入 `1` 选择华为云优先，`2` 选择中科大优先，`3` 查看配置。入口使用 WSL root 用户执行换源，脚本路径根据当前目录转换。若 WSL 注册名称不同，请修改 PowerShell 文件中的 `-d Debian`。

## 脚本如何处理现有源

1. 使用 Debian 官方 `python3-apt` 解析库读取 `/etc/apt/sources.list` 及 `/etc/apt/sources.list.d/` 下的 `.list`、`.sources` 配置。
2. 将原有 trixie 或 stable 系统源禁用。传统 `.list` 条目添加注释；Deb822 `.sources` 的系统段落设置 `Enabled: no`。原配置保留在原路径，另有备份。
3. 保留第三方源和原有 `deb-src` 启用需求。同一文件内的第三方条目也会保留。
4. 写入独立的双源配置，并运行 `apt-get update` 验证访问及签名。

新配置覆盖：

| 仓库 | Suite |
| --- | --- |
| 主仓库 | `trixie` |
| 常规更新 | `trixie-updates` |
| 安全更新 | `trixie-security` |
| Backports | `trixie-backports` |

所有仓库启用 `main contrib non-free non-free-firmware`。Backports 配置会启用；具体软件包是否安装或升级仍由 APT 的规则和后续命令决定。本脚本执行索引刷新。

## 双源与自动备用

| 镜像 | 主仓库地址 | 安全更新地址 |
| --- | --- | --- |
| 华为云 | `https://repo.huaweicloud.com/debian/` | `https://repo.huaweicloud.com/debian-security/` |
| 中科大 | `https://mirrors.ustc.edu.cn/debian/` | `https://mirrors.ustc.edu.cn/debian-security/` |

脚本使用 [APT 的镜像列表机制](https://manpages.debian.org/trixie/apt/apt-transport-mirror.1.en.html)。优先镜像设置为 `priority:1`，备用镜像为 `priority:2`；文件下载失败时，APT 尝试另一个镜像。两家均无法提供所需文件时，刷新会失败。

有效配置写入：

```text
/etc/apt/sources.list.d/wsl-mirrors.sources
/etc/apt/mirrors/wsl-debian.list
/etc/apt/mirrors/wsl-debian-security.list
```

两家都使用 HTTPS，系统仓库保留 Debian archive keyring 签名校验。

## 自动安装依赖

若缺少 `python3-apt`、`patch` 或 `diffutils`，脚本通过临时华为云/中科大源安装这些工具及其必要依赖。临时源和索引放在 `/tmp/apt-mirror-bootstrap-*`，安装过程不修改原有软件源配置。

自动安装需要 root 权限、可用网络和有效的 Debian keyring。工具安装产生的软件包变更不会随换源配置回滚。

## 备份与失败回滚

每次换源前，脚本把涉及的配置备份到：

```text
/var/backups/apt-mirror-switch-*/
```

备份包含 `restore-map.txt`，记录各备份文件的原路径。文件所有者及权限随备份保留。

索引刷新失败时，脚本恢复原配置，隔离本次新增的配置文件，并返回非零退出状态。原有第三方源若自身不可用，也会导致完整刷新失败并触发回滚。若检测到配置被并发修改，回滚会暂停并报告备份位置，避免覆盖其他操作。

脚本通过文件锁阻止并发切换，并在配置变更前检查 APT/dpkg 锁。诊断临时文件和备份保留，便于核查。

## 适用范围

- 固定配置 Debian 13；运行前请自行确认目标系统版本。
- 支持常见官方和镜像地址，其仓库路径以 `/debian` 或 `/debian-security` 结尾，以及 Debian 安装介质源和本脚本旧版镜像列表。
- 匹配 `trixie`、`trixie-updates`、`trixie-security`、`trixie-backports` 及对应 `stable` 名称。其他发行版 Suite 或特殊仓库路径不会自动禁用，需人工核查。
- 遇到软链接、脚本管理文件被外部改写，或同一个 Deb822 段落混有系统源与第三方地址时，停止并报告。
- 不包含 WSL 服务或 `systemd-binfmt` 修复。

## 验证

在 Debian 13 环境中运行：

```bash
sudo /usr/bin/python3 verify-switch.py
```

验证脚本在独立 `/tmp/verify-wsl-mirror-*` 配置中测试，不修改系统软件源。覆盖：

- 首次配置和已有配置的索引刷新失败回滚。
- 并发切换锁保护。
- 官方源、阿里云源及混合第三方条目的 `.list` / `.sources` 文件。
- 无尾换行、第三方 CRLF 文件和重复执行。
- 旧版 `mirror+file` 条目的迁移。

当前 Debian 13 WSL 已实际验证新版配置写入、索引刷新和第三方源保留。其他机器尚未逐台验证。
