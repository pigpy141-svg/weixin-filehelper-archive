# -*- coding: utf-8 -*-
"""解析并归档微信 4.x 转发收藏笔记卡片（appmsg type=24，内嵌图文/视频）。"""
import os
import json
import re
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import struct
import hashlib
import html
import xml.etree.ElementTree as ET
from Crypto.Cipher import AES

# Windows: 防止 ffmpeg 等子进程弹出黑色控制台窗口
_NO_WIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
_SP_KW = {"creationflags": _NO_WIN} if os.name == "nt" else {}

CODE = 1314233902
WXID = os.environ.get("WECHAT_WXID", "")
XOR = CODE & 0xFF
_NEW_IMAGE_KEYS = None
_NEW_IMAGE_KEYS_LOADED = False


def image_key(wxid):
    if not wxid:
        raise ValueError("缺少微信原始 wxid，无法解密图片缓存")
    return hashlib.md5(f"{CODE}{wxid}".encode()).hexdigest()[:16].encode()


def set_wxid(wxid):
    global WXID, KEY
    WXID = wxid or ""
    KEY = image_key(WXID) if WXID else b""

KEY = image_key(WXID) if WXID else b""
IMAGE_MAGIC = b"\x07\x08V2\x08\x07"


def image_ext(plain):
    if plain.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if plain.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if plain.startswith(b"RIFF") and plain[8:12] == b"WEBP":
        return "webp"
    if plain.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return None


def load_new_image_keys():
    """微信 4.1.13+ 图片缓存改用 wx_key 返回的 AES/XOR 密钥。"""
    global _NEW_IMAGE_KEYS, _NEW_IMAGE_KEYS_LOADED
    if _NEW_IMAGE_KEYS_LOADED:
        return _NEW_IMAGE_KEYS
    _NEW_IMAGE_KEYS_LOADED = True
    try:
        import wx_key
        data = json.loads(wx_key.get_image_key() or "{}")
    except Exception:
        return None
    result = {}
    for account in data.get("accounts", []):
        wxid = account.get("wxid")
        for item in account.get("keys", []):
            aes = item.get("aesKey")
            xor_value = item.get("xorKey")
            if not wxid or not aes or xor_value is None:
                continue
            aes_bytes = aes.encode("ascii") if isinstance(aes, str) else bytes(aes)
            if len(aes_bytes) >= 16 and 0 <= int(xor_value) <= 255:
                result[wxid] = (aes_bytes[:16], int(xor_value))
    _NEW_IMAGE_KEYS = result
    return _NEW_IMAGE_KEYS


def decrypt_new_wechat_image(data):
    if not WXID or len(data) < 15 or data[:6] != IMAGE_MAGIC:
        return None, None
    pair = (load_new_image_keys() or {}).get(WXID)
    if not pair:
        return None, None
    aes_key, xor_value = pair
    _, aes_size, _ = struct.unpack("<6sLLx", data[:15])
    encrypted = data[15:]
    padded = aes_size + (-aes_size % 16)
    head = AES.new(aes_key, AES.MODE_ECB).decrypt(encrypted[:padded])[:aes_size]
    tail = bytes(value ^ xor_value for value in encrypted[aes_size:])
    plain = head + tail
    return plain, image_ext(plain)

TOKEN_RE = re.compile(rb"[0-9a-f]{16}")

TEXT_TYPES = {"1", "2", "3", "5", "6", "7"}
VIDEO_TYPES = {"4"}
HTML_TYPES = {"8"}
IMAGE_TYPES = {"2", "3", "5"}


def strip_sender_prefix(text):
    if not text:
        return text
    if ":\n" in text[:96]:
        head, sep, rest = text.partition(":\n")
        if head.startswith("wxid_") or "@" in head:
            return rest
    return text


def child_text(node, name):
    child = node.find(name)
    return (child.text or "").strip() if child is not None and child.text else ""


def clean_text(value):
    value = html.unescape(value or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def decrypt_wechat_image(data, wxid=None):
    if wxid:
        set_wxid(wxid)
    if not WXID:
        return None, None
    if len(data) < 15 or data[:6] != IMAGE_MAGIC:
        return None, None
    plain, ext = decrypt_new_wechat_image(data)
    if plain:
        return plain, ext
    _, aes_size, _ = struct.unpack("<6sLLx", data[:15])
    encrypted = data[15:]
    padded = aes_size + (-aes_size % 16)
    cipher = AES.new(KEY, AES.MODE_ECB)
    head = cipher.decrypt(encrypted[:padded])[:aes_size]
    tail = bytes(value ^ XOR for value in encrypted[aes_size:])
    plain = head + tail
    return plain, image_ext(plain)


def packed_token(packed):
    if not packed:
        return None
    match = TOKEN_RE.search(packed if isinstance(packed, bytes) else packed.encode("utf-8", "ignore"))
    return match.group(0).decode() if match else None


def parse_record_xml(text):
    text = strip_sender_prefix(text)
    try:
        root = ET.fromstring(text)
    except Exception:
        return None
    appmsg = root.find(".//appmsg")
    if appmsg is None:
        return None
    app_type_text = child_text(appmsg, "type")
    if app_type_text != "24":
        return None
    record_node = appmsg.find("recorditem")
    if record_node is None or not record_node.text:
        return None
    try:
        record = ET.fromstring(record_node.text)
    except Exception:
        return None
    items = []
    for index, item in enumerate(record.findall(".//dataitem"), start=1):
        data_type = item.attrib.get("datatype", "")
        entry = {
            "index": index,
            "kind": "text" if data_type in TEXT_TYPES else
                   "video" if data_type in VIDEO_TYPES else
                   "html" if data_type in HTML_TYPES else
                   "image" if data_type in IMAGE_TYPES else "other",
            "datatype": data_type,
            "dataid": item.attrib.get("dataid", ""),
            "htmlid": item.attrib.get("htmlid", ""),
            "datafmt": item.attrib.get("datafmt", ""),
        }
        for child in item:
            tag = child.tag
            value = child.text or ""
            if tag == "datadesc":
                entry["text"] = clean_text(value)
            elif value:
                entry[tag] = value.strip()
        duration = entry.get("duration")
        if duration and duration.isdigit():
            entry["duration"] = int(duration)
        size = entry.get("datasize")
        if size and size.isdigit():
            entry["datasize"] = int(size)
        thumb_size = entry.get("thumbsize")
        if thumb_size and thumb_size.isdigit():
            entry["thumbsize"] = int(thumb_size)
        items.append(entry)
    title = child_text(appmsg, "title")
    description = clean_text(child_text(appmsg, "des"))
    url = child_text(appmsg, "url")
    text_parts = [item.get("text", "") for item in items if item.get("text")]
    full_text = "\n\n".join(part for part in text_parts if part) or description
    return {
        "kind": "wechat_note",
        "app_type": 24,
        "title": title,
        "description": description,
        "url": url,
        "items": items,
        "full_text": full_text,
        "video_count": sum(1 for item in items if item.get("kind") == "video"),
        "html_count": sum(1 for item in items if item.get("kind") == "html"),
    }


def iter_note_files(note_dir):
    for kind_dir, kind in (("V", "video"), ("Img", "thumb"), ("Dat", "html")):
        base = note_dir / kind_dir
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                yield kind, path


def find_note_dir(attach_root, chat_safe, create_time, token):
    attach_root = Path(attach_root)
    if not token:
        return None
    month = None
    try:
        import time
        month = time.strftime("%Y-%m", time.localtime(int(create_time)))
    except Exception:
        month = None
    candidates = []
    if month:
        candidates.append(attach_root / chat_safe / month / "Rec" / token)
    rec_root = attach_root / chat_safe
    if rec_root.is_dir():
        for month_dir in rec_root.iterdir():
            candidate = month_dir / "Rec" / token
            if candidate.is_dir():
                candidates.append(candidate)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def index_local_media(note_dir):
    videos = {}
    partial_videos = {}
    thumbs = {}
    htmls = {}
    for kind, path in iter_note_files(note_dir):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        stem = path.stem
        if kind == "video":
            info = {"path": str(path), "size": size}
            videos.setdefault(stem, info)
            partial_videos.setdefault(stem, info)
        elif kind == "thumb":
            thumbs.setdefault(stem, {"path": str(path), "size": size})
        elif kind == "html":
            htmls[len(htmls)] = {"path": str(path), "size": size}
    return videos, partial_videos, thumbs, htmls


def safe_filename(value):
    return re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" ._") or "note"


def copy_bytes(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as source, open(dst, "wb") as target:
        shutil.copyfileobj(source, target)


def _ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def extract_video_frame(video_path, dst, at_seconds=1):
    """从已保存的完整 MP4 抽一帧当干净封面。成功返回 True。"""
    exe = _ffmpeg_exe()
    if not exe or not os.path.exists(video_path):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cmd = [exe, "-y", "-ss", str(at_seconds), "-i", video_path,
           "-frames:v", "1", "-q:v", "3", dst]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **_SP_KW)
    except OSError:
        return False
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        return False
    return True


def ensure_browser_video(mp4_path):
    """把 HEVC(H.265) 的 MP4 转成浏览器可直接播放的 H.264。

    微信笔记里的视频常是 HEVC，Chromium 对 MP4 内 HEVC 支持不稳定（有声无画）。
    原件备份为同名 ``.hevc.mp4``；已是 H.264 或转码失败时保持原文件不变。
    返回编码信息字符串，仅供日志使用。
    """
    exe = _ffmpeg_exe()
    if not exe or not os.path.exists(mp4_path):
        return "skip"
    backup = mp4_path[:-4] + ".hevc.mp4"
    probe = subprocess.run([exe, "-i", mp4_path], capture_output=True, **_SP_KW)
    info = probe.stderr or b""
    if b"Video: hevc" not in info:
        return "already-h264"
    tmp = mp4_path + ".h264.mp4"
    cmd = [exe, "-y", "-i", mp4_path, "-c:v", "libx264", "-profile:v", "high",
           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
           "-movflags", "+faststart", tmp]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_SP_KW)
    except OSError:
        return "transcode-error"
    if result.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return "transcode-error"
    if not os.path.exists(backup):
        os.replace(mp4_path, backup)
    else:
        os.remove(mp4_path)
    os.replace(tmp, mp4_path)
    return "hevc->h264"


def transcode_one(path, progress=None):
    """对单个 mp4 做浏览器兼容转码；带可选进度回调。"""
    try:
        ensure_browser_video(path)
    finally:
        if progress:
            try:
                progress()
            except Exception:
                pass


def parallel_ensure_videos(paths, workers=3, progress=None):
    """并行转码多个 mp4；workers<=1 时顺序执行。"""
    paths = [p for p in paths if p]
    if not paths:
        return
    if workers <= 1:
        for pth in paths:
            transcode_one(pth, progress)
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as ex:
        futs = [ex.submit(transcode_one, pth, progress) for pth in paths]
        for f in futs:
            f.result()


def attach_note_media(note, note_dir, archive_note_dir, progress=None):
    videos, partial_videos, thumbs, htmls = index_local_media(note_dir)
    complete_sizes = {info["size"] for info in videos.values()}
    complete_names = set(videos)
    for stem, info in partial_videos.items():
        if stem in complete_names:
            continue
        if info["size"] in complete_sizes:
            continue
    video_items = [item for item in note["items"] if item.get("kind") == "video"]
    videos_by_size = {}
    for info in videos.values():
        videos_by_size.setdefault(info["size"], info)
    available_thumbs = list(thumbs.values())
    transcode_targets = []
    frame_jobs = []
    for ordinal, item in enumerate(video_items, start=1):
        htmlid = item.get("htmlid") or ""
        match = re.search(r"(\d+)$", htmlid)
        stem = match.group(1) if match else str(ordinal)
        expected = item.get("datasize")
        local = videos.get(stem)
        if local and expected and local["size"] != expected:
            local = None
        complete = bool(local)
        if not local and expected:
            candidate = videos_by_size.get(expected)
            if candidate:
                local = candidate
                complete = True
        if not local and stem in partial_videos:
            local = partial_videos[stem]
            complete = False
        if local:
            suffix = ".mp4" if complete else ".partial.mp4"
            rel_path = f"videos/{item['dataid']}{suffix}"
            dst = os.path.join(archive_note_dir, rel_path)
            # 只在缺失时复制；完整视频随后会被原地转成 H.264，
            # 不能再用源(HEVC)大小做比较，否则会反复覆盖回转码前的文件。
            if not os.path.exists(dst) or os.path.getsize(dst) == 0:
                copy_bytes(local["path"], dst)
            if complete:
                transcode_targets.append(dst)
                item["local_size"] = os.path.getsize(dst)
            else:
                item["local_size"] = local["size"]
            item["video"] = rel_path
            item["video_complete"] = complete
        rel_thumb = f"thumbs/{item['dataid']}.jpg"
        thumb_dst = os.path.join(archive_note_dir, rel_thumb)
        need_frame = False
        # 微信笔记自带的 V2 封面缩略图解密后常带周期性彩条，
        # 因此有完整视频时优先从视频抽一帧干净封面（稍后并行抽帧）。
        if item.get("video") and item.get("video_complete"):
            video_dst = os.path.join(archive_note_dir, item["video"])
            if not os.path.exists(thumb_dst) or os.path.getsize(thumb_dst) == 0:
                need_frame = True
                frame_jobs.append((item, rel_thumb, video_dst, thumb_dst))
            else:
                item["thumb"] = rel_thumb
    if frame_jobs:
        with ThreadPoolExecutor(max_workers=min(3, len(frame_jobs))) as ex:
            frame_ok = list(ex.map(lambda j: extract_video_frame(j[2], j[3]), frame_jobs))
        for (item, rel_thumb, vd, td), ok in zip(frame_jobs, frame_ok):
            if ok:
                item["thumb"] = rel_thumb
    # 对仍无封面的视频，回退解密原始 V2 缩略图（可能带彩条）。
    available_thumbs2 = list(thumbs.values())
    for item in video_items:
        if item.get("thumb"):
            continue
        expected_thumb_size = item.get("thumbsize")
        thumb = None
        if available_thumbs2 and expected_thumb_size:
            thumb_index, candidate = min(
                enumerate(available_thumbs2),
                key=lambda pair: abs(pair[1]["size"] - expected_thumb_size))
            if abs(candidate["size"] - expected_thumb_size) <= 128:
                available_thumbs2.pop(thumb_index)
                thumb = candidate
        if thumb:
            try:
                with open(thumb["path"], "rb") as source:
                    data = source.read()
                plain, ext = decrypt_wechat_image(data)
                if plain:
                    rel_path = f"thumbs/{item['dataid']}.{ext}"
                    dst = os.path.join(archive_note_dir, rel_path)
                    if not os.path.exists(dst) or os.path.getsize(dst) != len(plain):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with open(dst, "wb") as output:
                            output.write(plain)
                    item["thumb"] = rel_path
            except OSError:
                pass
    for item in note["items"]:
        if item.get("kind") != "html":
            continue
        if htmls:
            local = htmls[sorted(htmls)[0]]
            rel_path = "html/note.html"
            dst = os.path.join(archive_note_dir, rel_path)
            if not os.path.exists(dst) or os.path.getsize(dst) != local["size"]:
                copy_bytes(local["path"], dst)
            item["html"] = rel_path
            item["local_size"] = local["size"]
            break
    # 先并行转码所有完整视频，再据此抽帧/统计
    workers = int(os.environ.get("TRANSCODE_WORKERS", "3") or "3")
    def _tick(*_):
        if progress:
            try: progress()
            except Exception: pass
    parallel_ensure_videos(transcode_targets, workers=workers, progress=_tick)
    # 转码后回填每个 item 的真实大小
    for item in video_items:
        if item.get("video") and item.get("video_complete"):
            vp = os.path.join(archive_note_dir, item["video"])
            if os.path.exists(vp):
                item["local_size"] = os.path.getsize(vp)
    available = sum(1 for item in video_items if item.get("video") and item.get("video_complete"))
    partial = sum(1 for item in video_items if item.get("video") and not item.get("video_complete"))
    note["media_summary"] = {
        "videos_total": len(video_items),
        "videos_complete": available,
        "videos_partial": partial,
        "thumbs": sum(1 for item in video_items if item.get("thumb")),
        "html": any(item.get("html") for item in note["items"]),
    }
    return note


def note_dir_name(chat, local_id, token):
    return safe_filename(f"{chat}__{local_id}__{token or 'unknown'}")


def message_key(chat, local_id):
    return f"{chat}:{local_id}"
