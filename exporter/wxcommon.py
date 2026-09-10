# -*- coding: utf-8 -*-
"""微信 4.x 解密数据库 -> Markdown / JSON 导出器（只读，离线）。"""
import sqlite3, os, re, json, time, hashlib, html
import zstandard as zstd

ZSTD_MAGIC = b"(\xb5/\xfd"
_zdec = zstd.ZstdDecompressor()

def decode_content(val):
    """message_content 可能是明文 str，也可能是 zstd 压缩的 blob。返回 utf8 文本或 None。"""
    if val is None:
        return None
    if isinstance(val, str):
        b = val.encode("utf-8", "replace")
    else:
        b = bytes(val)
    if b[:4] == ZSTD_MAGIC:
        try:
            b = _zdec.decompress(b)
        except Exception:
            return None
    try:
        return b.decode("utf-8", "replace")
    except Exception:
        return None

TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频",
    47: "表情", 48: "位置", 49: "链接/文件", 50: "语音/视频通话",
    10000: "系统", 10002: "系统",
}

def base_type(t):
    return t & 0xFFFF if isinstance(t, int) else t

def type_label(t):
    bt = base_type(t)
    if bt in (49,):
        return "链接/文件"
    return TYPE_NAMES.get(bt, f"类型{t}")

def extract_app_fields(text):
    """从 type=49 的 XML 里提取标题/描述/链接/文件名。"""
    out = {}
    def grab(tag):
        m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), text, re.S | re.I)
        if m:
            return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        return None
    out["title"] = grab("title")
    out["des"] = grab("des")
    out["url"] = grab("url")
    out["filename"] = None
    m = re.search(r'<a[^>]+>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</a>', text, re.S | re.I)
    if m:
        out["filename"] = html.unescape(m.group(1)).strip()
    return out

def md5table(uname):
    return "Msg_" + hashlib.md5(uname.encode("utf-8")).hexdigest()

def connect(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)
