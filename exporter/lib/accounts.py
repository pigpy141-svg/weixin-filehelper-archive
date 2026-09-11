# -*- coding: utf-8 -*-
"""多微信账号发现、密钥校验与本地路径管理。

项目不绑定某一个微信号：只要 xwechat_files 下存在账号数据目录，
就为每个账号单独维护密钥、解密缓存、归档和增量高水位线。
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "wechat-decrypt"
DEFAULT_FILES_ROOT = Path.home() / "Documents" / "xwechat_files"
SKIP_NAMES = {"all users"}
SAFE_RE = re.compile(r"[^A-Za-z0-9_@.-]+")


def expand(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value or "")))).resolve()


def files_root(config=None):
    """微信账号根目录，默认 Documents/xwechat_files。"""
    config = config or {}
    value = config.get("wechat_files_dir") or config.get("xwechat_dir")
    if value:
        path = expand(value)
        if path.is_dir():
            return path
    return DEFAULT_FILES_ROOT


def wxid_from_dir(path):
    name = Path(path).name
    if not name.lower().startswith("wxid_"):
        return None
    # 形如 wxid_abc_c47a：最后一段短后缀是本地账号实例，前面 wxid_abc 才用于图片密钥。
    parts = name.rsplit("_", 1)
    suffix = parts[1] if len(parts) == 2 else ""
    if len(suffix) == 4 and suffix.isalnum():
        return parts[0]
    return name


def account_slug(wxid):
    return SAFE_RE.sub("_", wxid).strip("._-") or "account"


def discover_accounts(config=None):
    """返回本机已有微信数据目录，按最近修改时间倒序。"""
    root = files_root(config)
    accounts = []
    if not root.is_dir():
        return accounts
    for path in root.iterdir():
        if not path.is_dir() or path.name.lower() in SKIP_NAMES:
            continue
        wxid = wxid_from_dir(path)
        if not wxid:
            continue
        db = path / "db_storage" / "message" / "message_0.db"
        if not db.is_file():
            continue
        stat = db.stat()
        accounts.append({
            "wxid": wxid,
            "slug": account_slug(wxid),
            "dir": path,
            "db": db,
            "storage": path / "db_storage",
            "msg": path / "msg",
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        })
    seen = {}
    for account in accounts:
        base = account["slug"]
        if base not in seen:
            seen[base] = 1
            continue
        seen[base] += 1
        account["slug"] = account_slug(f"{base}_{seen[base]}")
    accounts.sort(key=lambda a: a["mtime"], reverse=True)
    return accounts


def active_accounts(config=None, within_hours=12):
    """当前可能已登录的账号：消息库在近一段时间内被写过。"""
    import time
    cutoff = time.time() - within_hours * 3600
    accounts = discover_accounts(config)
    fresh = [a for a in accounts if a["mtime"] >= cutoff]
    return fresh or accounts[:1]


def archive_root(config=None):
    config = config or {}
    value = config.get("archive_dir") or (ROOT / "local" / "archive")
    path = expand(value)
    return path if path.is_absolute() else (ROOT / value).resolve()


def account_archive(account, config=None):
    return archive_root(config) / "accounts" / account["slug"]


def decrypted_dir(account, config=None):
    base = config.get("decrypted_dir") if config else None
    base = expand(base) if base else (ROOT / "local" / "decrypted")
    return base / account["slug"]


def key_path(account, config=None):
    base = config.get("keys_dir") if config else None
    base = expand(base) if base else (ROOT / "local" / "keys")
    return base / (account["slug"] + ".json")


def load_config(path=None):
    path = Path(path or (ROOT / "local" / "config.json"))
    if not path.is_file():
        return {}
    return json.load(open(path, encoding="utf-8"))


def _crypto():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from decrypt_all import derive_enc_key, verify_page1
    return derive_enc_key, verify_page1


def read_db_key(path):
    data = json.load(open(Path(path), encoding="utf-8"))
    key = str(data.get("db_key") or "").strip()
    if len(key) != 64 or any(c not in "0123456789abcdefABCDEF" for c in key):
        return None
    return key.lower()


def key_matches_account(key_hex, account):
    """用 message_0.db 首页 HMAC 判断密钥是否属于该账号。"""
    try:
        derive_enc_key, verify_page1 = _crypto()
        with open(account["db"], "rb") as f:
            page1 = f.read(4096)
        salt = page1[:16]
        enc_key = derive_enc_key(bytes.fromhex(key_hex), salt)
        return bool(verify_page1(enc_key, salt, page1))
    except Exception:
        return False


def account_has_valid_key(account, config=None):
    path = key_path(account, config)
    if not path.is_file():
        return False
    key = read_db_key(path)
    return bool(key and key_matches_account(key, account))


def find_matching_key(account, config=None):
    """在 per-account keys 及旧版 local/key_result.json 中寻找可用密钥。"""
    path = key_path(account, config)
    if path.is_file():
        key = read_db_key(path)
        if key and key_matches_account(key, account):
            return key, path
    legacy = ROOT / "local" / "key_result.json"
    if legacy.is_file():
        key = read_db_key(legacy)
        if key and key_matches_account(key, account):
            return key, legacy
    return None, None
