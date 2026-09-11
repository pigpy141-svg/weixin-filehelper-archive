# 微信文件传输助手本地归档器


一个仅运行在本机、面向微信 4.x（Windows）的个人资料归档工具。它只归档“文件传输助手”中**首次建立基线之后新产生的消息**，支持文字、图片、视频、普通文件，以及包含图文和视频的微信笔记卡片。

## 功能

- 只监听并保存“文件传输助手”，不处理其他聊天会话。
- 首次运行只建立高水位基线，不回溯、不批量导出旧消息。
- 后续运行只读取 `local_id` 大于基线的新增消息。
- 定点复制新增消息关联的图片、视频、文档和笔记媒体，不做全局媒体扫描。
- 本地网页浏览器仅监听 `127.0.0.1`，可按时间或文件夹方式查看。
- 支持文件标签、多标签归类和批量打标签。
- HEVC 视频可自动转码为浏览器更容易播放的 H.264 MP4。
- 数据、密钥和配置默认全部放在 `local/`，该目录不会被 Git 上传。

## 重要边界

- 本项目只适合备份**你本人有权访问的微信数据**。
- 不会主动联网，不包含云同步、上传或账号密码收集功能。
- 数据库密钥是敏感信息，只保存在本机 `local/keys/`，整个 `local/` 目录默认不上传。
- 微信版本、数据库结构或客户端缓存策略变化时，工具可能失效。
- 建议先小范围验证，再把 `local/archive` 纳入你自己的备份方案。

## 运行环境

- Windows 10/11
- Python 3.10–3.14（Windows x64）
- 已登录并保持解锁状态的微信 4.x Windows 客户端
- Git（用于拉取一个第三方密钥分析依赖）

## 目录结构

```text
exporter/                 增量导出、微信笔记解析、媒体处理
viewer/                   本地网页浏览器
tools/wechat-decrypt/     SQLCipher 数据库解密辅助代码（第三方，保留其许可证）
scripts/                  Windows PowerShell 辅助脚本
config.example.json       配置模板
local/                    本机密钥、临时解密库和归档数据（不上传）
```

## 安装

在 PowerShell 中进入项目目录：

```powershell
scripts\setup.ps1
```

该脚本会创建 `.venv`、安装 Python 依赖、克隆第三方仓库：

```text
https://github.com/LifeArchiveProject/WeChatDataAnalysis
```

安装脚本会自动切到已验证的固定提交 `36d1e548172e9fb87f26d729403a058d13d92bca`，避免上游改动导致路径不兼容。

如果当前网络无法直接访问 GitHub，请先在同一 PowerShell 窗口设置代理，再运行安装脚本，例如：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
scripts\setup.ps1
```

然后按需编辑 `local/config.json`。多数情况下可以保持默认：

```json
{
  "wechat_files_dir": "C:/Users/你的用户名/Documents/xwechat_files",
  "archive_dir": "local/archive",
  "dll_path": "",
  "port": 8765
}
```

字段说明：

- `wechat_files_dir`：微信 4.x 的账号根目录。默认会自动使用 `Documents/xwechat_files`，不需要填写具体微信号。
- `dll_path`：可选。默认留空，脚本会在常见安装目录中寻找最新的 `Weixin.dll`。
- `archive_dir`：归档目录。相对路径会相对于项目根目录解析，默认 `local/archive`。
- `port`：本地网页端口，默认 `8765`。

项目会自动扫描根目录下所有形如 `wxid_...` 的账号数据目录，并按账号分别保存。你登录哪一个微信号，就处理哪一个账号；切换账号后重新同步即可，不同账号的文件传输助手互不混淆。旧版配置中的 `account_dir`、`wxid`、`key_json` 仍可作为兼容字段使用，但不再是必填项。

## 第一次使用

保持微信登录并解锁，然后运行：

```powershell
scripts\init_baseline.ps1
```

这一步会：

1. 自动发现本机微信账号目录。
2. 从当前运行的微信进程提取近期活跃账号的数据库密钥，分别保存到 `local/keys/<wxid>.json`。
3. 为每个账号解密主消息库到 `local/decrypted/<wxid>/`。
4. 把各账号当前文件传输助手的最新消息 ID 写入各自基线。
5. 不导出任何基线之前的旧消息。

之后在文件传输助手发送或接收的消息，才会进入归档。首次遇到的新账号也会先建立基线，避免遍历历史消息。

如果微信 4.1.13 提示所有 `Weixin.exe` 进程“不可读”，请运行 `scripts\\extract_key_admin.ps1`（会弹出 UAC 提权窗口），或用“管理员身份运行 PowerShell”后再执行初始化脚本。项目只做本地只读内存检查，密钥只写入本机 `local/keys/`。

## 增量保存

命令行手动同步近期活跃账号：

```powershell
scripts\sync_once.ps1
```

也可以先启动本地查看器，再点击页面上的“同步”按钮。快速入库会先保存文字、图片、文件和笔记索引；视频兼容转码在后台继续执行。默认只处理近期有数据库写入的账号，避免每次扫描全部历史账号。需要检查所有账号时可运行：

```powershell
.\.venv\Scripts\python.exe exporter\sync_accounts.py --all
```

## 打开查看器

前台运行：

```powershell
scripts\viewer.ps1
```

后台运行（不保留控制台窗口）：

```powershell
wscript scripts\viewer_bg.vbs
```

打开：

```text
http://127.0.0.1:8765/
```

左侧会为每个已有归档的微信号显示一个“文件传输助手”会话，标题中带有对应 `wxid`，媒体、笔记和标签都按账号隔离。

## 归档内容

默认保存到 `local/archive/accounts/<wxid>/`：

- `messages.jsonl`：一行一条消息的索引和正文。
- `filehelper_state.json`：该账号的增量高水位线。
- `files/YYYY-MM/`：普通文件。
- `images/YYYY-MM/`：解密后的图片。
- `videos/YYYY-MM/`：视频和封面。
- `notes/`：微信笔记的 `note.json`、`note.md`、图片、HTML 和视频。

全局标签索引保存在 `local/archive/tags.json`，标签归属键包含账号和消息 ID，因此不同账号即使消息 ID 相同也不会串标签。密钥位于 `local/keys/`，解密缓存位于 `local/decrypted/`。整个 `local/` 目录都已被 `.gitignore` 排除。

## 手动命令

如果不用脚本，也可以直接运行：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe tools\extract_key.py --active-only
.\.venv\Scripts\python.exe -u exporter\sync_accounts.py --active-only --init-baseline
```

以后同步时去掉 `--init-baseline`：

```powershell
.\.venv\Scripts\python.exe -u exporter\sync_accounts.py --active-only
```

只处理一个账号时加 `--wxid wxid_xxxxxxxx`；检查所有已发现账号时用 `--all`。

## 第三方组件

- `tools/wechat-decrypt/` 来自第三方 wechat-decrypt 项目，保留其 `LICENSE` 和说明文件。
- 密钥提取和微信 4.1.13 图片密钥读取使用 [LifeArchiveProject/WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) 固定提交中的少量模块；安装脚本会单独克隆该仓库，并从其 `tools/key_wheels/` 安装与当前 Python 版本匹配的 `wx_key` wheel。
- 本仓库不复制、不分发 WeChatDataAnalysis 源码或 `wx_key` 二进制；它们的版权、许可和使用条款由各自上游项目保留。公共使用或再分发前，请自行复核上游许可和当地法律法规。
- Python 依赖见 `requirements.txt`；密钥和图片缓存解密能力面向 Windows x64。

## 隐私建议

- 不要提交 `local/`、`.venv/` 或微信原始数据目录。
- 不要把数据库密钥、截图、日志或归档文件发给不可信对象。
- 如果要公开发布自己的改造版本，先运行敏感信息搜索：

```powershell
rg -n "wxid_|key_result|db_key|xwechat_files|C:/Users" . -g "!local/**" -g "!tools/WeChatDataAnalysis/**"
```

## 免责声明

本项目仅用于个人数据备份、学习和研究。使用者需自行确认符合所在地区法律法规、微信服务条款以及对相关数据的合法访问权限。作者不对数据丢失、账号风险、解读错误或任何直接/间接损失承担责任。使用前请自行备份重要数据。
