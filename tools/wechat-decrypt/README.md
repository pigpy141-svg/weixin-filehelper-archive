# 微信 4.1.12 (Windows) 数据库解密完整方案

> 解决 wechat-decrypt 在微信 4.1.11+ 失效的问题（[Issue #2](https://github.com/328336690/wechat-decrypt/issues/2)）
>
> 已在 **微信 4.1.12.26 (x64, Windows 7)** 上验证，**25/25 数据库全部解密成功**

## 背景

社区工具 `wechat-decrypt` 通过扫描进程内存中的 `x'<64hex_enc_key><32hex_salt>'` 模式提取密钥。
但在微信 **4.1.11+** 中此模式**不再存在**（密钥缓存格式变更），导致工具失效。

## 核心发现

1. **密钥格式变了**：4.1.11+ 不再缓存 `x'<hex>'` 字符串，**password（32 字节原始密钥）以二进制形式**存在于 codec 配置函数的参数中
2. **password ≠ enc_key**：password 需要经过 **KDF 派生**才是加密用的 enc_key（之前的工具直接拿缓存值当密钥验证，参数不对必然失败）
3. **KDF 参数**：`PBKDF2-HMAC-SHA512, 256000 iterations, salt = 数据库文件头 16 字节`，派生 32 字节 enc_key
4. **加密参数**（SQLCipher 4）：
   - `AES-256-CBC + HMAC-SHA512`
   - `page_size = 4096, reserve = 80`（IV 16 字节 @ offset 4016，HMAC 64 字节 @ offset 4032）
   - 布局：`[0:16]=salt | [16:4016]=加密数据 | [4016:4032]=IV | [4032:4096]=HMAC`
5. **HMAC 验证**（SQLCipher 4 标准）：
   ```
   mac_salt = salt XOR 0x3a
   mac_key = PBKDF2-HMAC-SHA512(enc_key, mac_salt, 2, 32)
   valid = HMAC-SHA512(mac_key, page1[16:4032] + pack('<I', 1)) == page1[4032:4096]
   ```

## 密钥获取方法（Frida）

微信 4.1.12 的 codec 配置函数引用 "MMV1" 魔数字符串，位于 `weixin.dll` 偏移 **0x3486140**（4.1.12.26）。

**关键点**：
- 必须用 **Frida spawn**（进程启动早期注入），attach 会错过密钥设置
- hook 该函数入口时，**rcx 指向的结构前 32 字节就是 password**
- ⚠️ 该路径存在 **anti-hook 检测**（hook 后微信可能弹错误报告崩溃），但 spawn 早期 hook 可以捕获

```python
# 伪代码
import frida
pid = frida.spawn(r"C:\Program Files\Tencent\Weixin\Weixin.exe")
session = frida.attach(pid)
script = session.create_script("""
    var base = Process.getModuleByName('Weixin.dll').base;
    Interceptor.attach(base.add(0x3486140), {
        onEnter: function(args) {
            // args[0] (rcx) 指向的结构前 32 字节 = password
            console.log('PASSWORD:', hexdump(args[0], {length: 64}));
        }
    });
""")
script.load()
frida.resume(pid)  # 微信启动 -> 密钥设置 -> 捕获
```

**结构说明**：rcx 指向的 codec 配置结构 = `password[32] + 其他字段`，触发时机为数据库打开（进程启动早期）。

## 数据库解密

```python
import hashlib
from Crypto.Cipher import AES

PASSWORD = bytes.fromhex("<获取到的 64 位 hex>")  # 32 字节主密钥

def derive_enc_key(salt):
    return hashlib.pbkdf2_hmac("sha512", PASSWORD, salt, 256000, dklen=32)

def decrypt_page(enc_key, page_data, pgno):
    iv = page_data[4016:4032]
    if pgno == 1:
        encrypted = page_data[16:4016]
        page = b'SQLite format 3\x00' + AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted) + b'\x00'*80
    else:
        encrypted = page_data[:4016]
        page = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted) + b'\x00'*80
    return page
```

- 每个数据库**独立 salt**（文件头 16 字节），用同一个 password 派生各自的 enc_key

### 数据解析（关键坑！）

**1. 表名映射**：`Msg_<MD5(wxid)>`，通过 `Name2Id` 表（user_name ↔ rowid）确认。示例：`MD5("wxid_xxx")` = 表名 hash。

**2. ⚠️ 发送者（real_sender_id）逐库不同！** 这是最容易踩的坑：
- **message_0（最新库）**：`real_sender_id` = `Name2Id` 表的 rowid（如 102 = 某 wxid，3 = 另一 wxid）
- **旧库（message_1/2/3 等）**：`real_sender_id` = 会话内角色（1/2），**且不同库的 1/2 含义可能相反！**
- **必须用"锚点句"逐库验证**：找双方都确认过的句子（如某句确定是用户发的），确定该库的 sender 映射后再导出
- 实测：message_1/2 的 sender1=用户；message_3 的 sender1=对方（相反！）

**3. 消息类型**：`local_type` 字段
- `1` = 文本（message_content 明文）
- `47` = 表情（XML，含 alias）
- `3/34/43` = 图片/语音/视频（content 是 protobuf，需额外解析）
- `10000` = 系统消息（撤回等）

**4. 其他**：WAL 文件帧格式 = `pgno(4,大端)+unk(4)+salt(16)+page(4096)`（解密：用**主库 enc_key**，IV=页[4016:4032]，加密范围[0:4016]——帧 salt 只是密钥轮换检测，实际用主库密钥）；手机备份 Backup.db 用手机端独立密钥，PC 端无法解密

## 验证结果

| 项目 | 结果 |
|------|------|
| 微信版本 | 4.1.12.26 (x64, Win7) |
| 数据库 | 25/25 全部解密成功 |
| 单库 | message_0~3.db 共数万条会话消息完整导出 |
| 发送者 | 100% 精确（数据库字段，非推测） |

## 附带发现

- **WAL 文件**：SQLCipher 4 的 WAL 帧格式 = `pgno(4, 大端) + unk(4, 大端) + salt(16) + page(4096)`，解密用**主库 enc_key**（IV=页[4016:4032]，加密范围[0:4016]），帧 salt 仅用于密钥轮换检测
- **手机备份 Backup.db**：使用手机端独立密钥，电脑端无法解密

## 文件

- `find_key.py` — Frida spawn 密钥获取脚本
- `decrypt_all.py` — 全库解密脚本
- `export_msgs.py` — 消息导出（含逐库 sender 验证示例）

## 致谢

本次破解由 **AI 辅助逆向** 完成：
- 逆向分析与脚本编写：DeepSeek V4 系列模型（v4-pro-preview → v4-flash-0731）
- 环境操作与验证：Windows 7 + Python 3.8 + Frida 15.2.2 + pycryptodome

**一个时代正在到来：AI 与人类协作，可以把"不可能"变成 48 小时内的"已完成"。**

## License

MIT
