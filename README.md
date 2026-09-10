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
- 数据库密钥是敏感信息，只保存在本机 `local/key_result.json`。
- 微信版本、数据库结构或客户端缓存策略变化时，工具可能失效。
- 建议先小范围验证，再把 `local/archive` 纳入你自己的备份方案。

## 运行环境

- Windows 10/11
- Python 3.10 或更高版本
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

然后编辑 `local/config.json`：

```json
{
  "account_dir": "C:/Users/你的用户名/Documents/xwechat_files/wxid_xxxxxxxx",
  "wxid": "wxid_xxxxxxxx",
  "key_json": "local/key_result.json",
  "archive_dir": "local/archive",
  "dll_path": "",
  "port": 8765
}
```

字段说明：

- `account_dir`：微信账号数据目录，通常是 `Documents/xwechat_files/` 下以 `wxid_` 开头的文件夹。
- `wxid`：原始微信 ID，通常是账号目录中去掉 `_xxxx` 后缀的部分；图片缓存解密需要它。
- `key_json`：提取出的数据库密钥保存位置，建议保持默认；支持相对项目根目录的路径。
- `dll_path`：可选。默认留空，脚本会在常见安装目录中寻找最新的 `Weixin.dll`。
- `archive_dir`：归档目录。相对路径会相对于项目根目录解析，默认 `local/archive`。
- `port`：本地网页端口，默认 `8765`。

## 第一次使用

保持微信登录并解锁，然后运行：

```powershell
scripts\init_baseline.ps1
```

这一步会：

1. 从当前运行的微信进程提取本机数据库密钥。
2. 解密主消息库到 `local/decrypted/`。
3. 把当前文件传输助手的最新消息 ID 写入基线。
4. 不导出任何基线之前的旧消息。

之后在文件传输助手发送或接收的消息，才会进入归档。

## 增量保存

命令行手动同步：

```powershell
scripts\sync_once.ps1
```

也可以先启动本地查看器，再点击页面上的“同步”按钮。快速入库会先保存文字、图片、文件和笔记索引；视频兼容转码在后台继续执行。

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

## 归档内容

默认保存到 `local/archive/`：

- `messages.jsonl`：一行一条消息的索引和正文。
- `filehelper_state.json`：增量高水位线。
- `tags.json`：标签和文件归属。
- `files/YYYY-MM/`：普通文件。
- `images/YYYY-MM/`：解密后的图片。
- `videos/YYYY-MM/`：视频和封面。
- `notes/`：微信笔记的 `note.json`、`note.md`、图片、HTML 和视频。

## 手动命令

如果不用脚本，也可以直接运行：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe tools\extract_key.py --config local\config.json
.\.venv\Scripts\python.exe -u exporter\save_new_filehelper.py `
  local\decrypted local\archive "$env:USERPROFILE\Documents\xwechat_files\你的账号\msg" `
  --wxid wxid_xxxxxxxx `
  --storage "$env:USERPROFILE\Documents\xwechat_files\你的账号\db_storage" `
  --key-json local\key_result.json --init-baseline
```

以后同步时去掉 `--init-baseline`，需要快速入库可加 `--fast`。

## 第三方组件

- `tools/wechat-decrypt/` 来自第三方 wechat-decrypt 项目，保留其 `LICENSE` 和说明文件。
- 密钥提取阶段使用 [LifeArchiveProject/WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) 的部分模块，安装脚本会单独克隆该仓库，不把它的代码复制进本仓库。
- Python 依赖见 `requirements.txt`。

## 隐私建议

- 不要提交 `local/`、`.venv/` 或微信原始数据目录。
- 不要把数据库密钥、截图、日志或归档文件发给不可信对象。
- 如果要公开发布自己的改造版本，先运行敏感信息搜索：

```powershell
rg -n "wxid_|key_result|db_key|xwechat_files|C:/Users" . -g "!local/**" -g "!tools/WeChatDataAnalysis/**"
```

## 免责声明

本项目仅用于个人数据备份、学习和研究。使用者需自行确认符合所在地区法律法规、微信服务条款以及对相关数据的合法访问权限。作者不对数据丢失、账号风险、解读错误或任何直接/间接损失承担责任。使用前请自行备份重要数据。
