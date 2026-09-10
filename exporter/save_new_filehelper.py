# -*- coding: utf-8 -*-
"""只增量保存“文件传输助手”(filehelper)在本项目启动之后新到达的消息。

设计原则：
- 高水位线 filehelper_state.json 记录已处理到的 local_id；首次运行只建立基线、不导出旧消息。
- 每次运行只解密主消息库 message_0.db，只读 filehelper 表里 local_id 更大的新增行。
- 仅定点复制这些新消息各自的图片/视频/文件，不遍历任何全局旧媒体、不碰其它会话。
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wxcommon import connect, decode_content, base_type, type_label, md5table, extract_app_fields
import note_cards
from note_cards import (ensure_browser_video, parse_record_xml,
                        packed_token, find_note_dir, attach_note_media,
                        note_dir_name, message_key)
from export_note_cards import note_markdown

CHAT = "filehelper"
CHAT_TITLE = "文件传输助手"
MAIN_DB = "message_message_0.db"
MD5RE = re.compile(rb"[0-9a-f]{32}")


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))


def month_of(ts):
    return time.strftime("%Y-%m", time.localtime(int(ts)))


def load_state(path):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def decrypt_main_db(enc_storage, key_hex, dec_path):
    """只解密主消息库 message/message_0.db -> dec_path。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "tools", "wechat-decrypt"))
    from decrypt_all import derive_enc_key, verify_page1, decrypt_db
    src = os.path.join(enc_storage, "message", "message_0.db")
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    with open(src, "rb") as f:
        page1 = f.read(4096)
    salt = page1[:16]
    enc_key = derive_enc_key(bytes.fromhex(key_hex), salt)
    if not verify_page1(enc_key, salt, page1):
        raise RuntimeError("主消息库 HMAC 校验失败（密钥不对或微信未解锁）")
    os.makedirs(os.path.dirname(dec_path), exist_ok=True)
    decrypt_db(src, dec_path, enc_key)


def packed_md5(packed):
    if not packed:
        return None
    raw = packed if isinstance(packed, (bytes, bytearray)) else packed.encode("utf-8", "ignore")
    m = MD5RE.search(bytes(raw))
    return m.group().decode() if m else None


def save_image(attach_root, md5, ym, out_dir):
    """定点解密 filehelper 自己目录下某张图片，返回归档内相对路径或 None。"""
    img_dir = os.path.join(attach_root, md5table(CHAT)[4:], ym, "Img")
    cands = [(md5 + "_h.dat", "image_h"), (md5 + ".dat", "image"), (md5 + "_t.dat", "image_t")]
    for fname, kind in cands:
        src = os.path.join(img_dir, fname)
        if not os.path.exists(src):
            continue
        plain, ext = note_cards.decrypt_wechat_image(open(src, "rb").read())
        if not plain:
            continue
        stem = md5 + ("_h" if kind == "image_h" else "_t" if kind == "image_t" else "")
        rel = os.path.join("images", ym, stem + "." + ext).replace("\\", "/")
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst) or os.path.getsize(dst) != len(plain):
            with open(dst, "wb") as f:
                f.write(plain)
        return rel
    return None


def _find_by_prefix(root, prefix, pred):
    """在 root 下按文件名前缀定点查找（命中即停），不做无谓全量遍历。"""
    if not os.path.isdir(root):
        return None
    for dp, _dn, files in os.walk(root):
        for n in files:
            if n.startswith(prefix) and pred(n):
                return os.path.join(dp, n)
    return None


def save_video(media_root, md5, ym, out_dir):
    """定点复制 filehelper 的视频（按月目录），HEVC 自动转 H.264。返回相对路径或 None。"""
    month_dir = os.path.join(media_root, "video", ym)

    def is_mp4(n):
        return n.lower().endswith(".mp4") and "_raw" not in n.lower()

    src = _find_by_prefix(month_dir, md5, is_mp4)
    if not src:  # 个别客户端可能放到相邻月份，兜底再查一次
        src = _find_by_prefix(os.path.join(media_root, "video"), md5, is_mp4)
    if not src:
        return None, None
    rel = None
    if src:
        rel = os.path.join("videos", ym, md5 + ".mp4").replace("\\", "/")
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst) or os.path.getsize(dst) == 0:
            shutil.copyfile(src, dst)
            ensure_browser_video(dst)

    def is_thumb(n):
        return n.lower().endswith((".jpg", ".jpeg", ".png")) and "thumb" in n.lower()

    thumb_src = _find_by_prefix(month_dir, md5, is_thumb)
    if not thumb_src:
        thumb_src = _find_by_prefix(os.path.join(media_root, "video"), md5, is_thumb)
    thumb_rel = None
    if thumb_src:
        ext = os.path.splitext(thumb_src)[1].lstrip(".") or "jpg"
        thumb_rel = os.path.join("videos", ym, md5 + "_thumb." + ext).replace("\\", "/")
        tdst = os.path.join(out_dir, thumb_rel)
        if not os.path.exists(tdst):
            shutil.copyfile(thumb_src, tdst)
    return rel, thumb_rel


def save_file(media_root, filename, ym, out_dir):
    """按“文件名 + 月份”定点复制收到的文件(appmsg type=6)。"""
    if not filename:
        return None
    month_dir = os.path.join(media_root, "file", ym)
    src = os.path.join(month_dir, filename)
    if not os.path.exists(src):
        hit = _find_by_prefix(month_dir, filename, lambda n: n == filename)
        if hit:
            src = hit
    if not os.path.exists(src):  # 相邻月份兜底
        src = _find_by_prefix(os.path.join(media_root, "file"), filename, lambda n: n == filename)
    if not src or not os.path.exists(src):
        return None
    rel = os.path.join("files", ym, filename).replace("\\", "/")
    dst = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
        shutil.copyfile(src, dst)
    return rel


def app_title(text):
    text = text or ""
    f = extract_app_fields(text)
    mtype = re.search(r"<type>\s*(\d+)\s*</type>", text, re.I)
    app_type = int(mtype.group(1)) if mtype else None
    mtitle = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    title = (mtitle.group(1).strip() if mtitle else "") or f.get("title")
    # 文件消息(appmsg type=6)的文件名在 <title>
    if app_type == 6 and title and not f.get("filename"):
        f["filename"] = title
    body = "[链接/文件] " + " | ".join(x for x in [f.get("filename"),
        (None if f.get("filename") == title else title), f.get("des"), f.get("url")] if x) \
        if (f.get("filename") or title or f.get("des") or f.get("url")) else "[链接/文件]"
    return f, body


def rebuild_note(conn, row, out_dir, attach_root, media_root, mtype, skip_video=False, progress=None):
    """根据 filehelper 的一行消息重建/刷新一条微信笔记卡片，返回 (note_ref, body)。"""
    lid, server_id, ct, raw, packed = row
    text = decode_content(raw)
    note = parse_record_xml(text or "")
    if not note:
        return None, None
    token = packed_token(decode_content(packed) if isinstance(packed, (bytes, bytearray)) else packed)
    folder = note_dir_name(CHAT, lid, token)
    note_target = os.path.join(out_dir, "notes", folder)
    os.makedirs(note_target, exist_ok=True)
    local_note_dir = find_note_dir(attach_root, md5table(CHAT)[4:], ct, token)
    # 若本地已有完整视频，删掉旧的(可能带横条)封面，强制改用视频抽帧得到干净封面
    have_video = bool(local_note_dir) and any(
        os.path.isfile(x) for x in glob.glob(os.path.join(str(local_note_dir), "V", "*.mp4")))
    if have_video:
        for old in glob.glob(os.path.join(note_target, "thumbs", "*")):
            try: os.remove(old)
            except OSError: pass
    if local_note_dir and not skip_video:
        note["local_cache"] = os.path.relpath(str(local_note_dir), media_root)
        attach_note_media(note, local_note_dir, note_target, progress=progress)
    else:
        note["local_cache"] = None
        if skip_video and local_note_dir:
            # 快速入库：先标记有缓存，转码留给后台 backfill
            note["media_summary"] = {
                "videos_total": note["video_count"], "videos_complete": 0,
                "videos_partial": 0, "thumbs": 0, "pending": True,
                "html": any(item.get("kind") == "html" for item in note["items"])}
        else:
            note["media_summary"] = {
                "videos_total": note["video_count"], "videos_complete": 0,
                "videos_partial": 0, "thumbs": 0,
                "html": any(item.get("kind") == "html" for item in note["items"])}
    self_wxid = conn.execute("SELECT user_name FROM Name2Id WHERE rowid=1").fetchone()[0]
    note_record = {
        "chat": CHAT, "local_id": lid, "server_id": server_id,
        "sender_id": self_wxid, "create_time": ct, "time": fmt_time(ct),
        "type_code": mtype, "token": token,
        "dir": os.path.join("notes", folder).replace("\\", "/"), "note": note}
    with open(os.path.join(note_target, "note.json"), "w", encoding="utf-8") as f:
        json.dump(note_record, f, ensure_ascii=False, indent=2)
    with open(os.path.join(note_target, "note.md"), "w", encoding="utf-8") as f:
        f.write(note_markdown(note, note_record))
    notes_index_path = os.path.join(out_dir, "notes_index.json")
    notes_index = json.load(open(notes_index_path, encoding="utf-8")) \
        if os.path.exists(notes_index_path) else {}
    notes_index[message_key(CHAT, lid)] = note_record
    with open(notes_index_path, "w", encoding="utf-8") as f:
        json.dump(notes_index, f, ensure_ascii=False, indent=2)
    body = note.get("full_text") or note.get("description") or ""
    return note_record["dir"], body


def backfill_notes(conn, dec_dir, out_dir, attach_root, media_root, progress_file=None):
    """检查之前缺完整视频的笔记；视频在微信里下载后自动补齐并刷新封面。

    progress_file: 若给定，持续写入 {total,done,current} 供前端读取。
    """
    notes_index_path = os.path.join(out_dir, "notes_index.json")
    if not os.path.exists(notes_index_path):
        return 0
    notes_index = json.load(open(notes_index_path, encoding="utf-8"))
    table = md5table(CHAT)
    updated = 0
    pending_keys = []
    for key, rec in notes_index.items():
        sm = (rec.get("note") or {}).get("media_summary") or {}
        total = sm.get("videos_total", 0)
        complete = sm.get("videos_complete", 0)
        pending = bool(sm.get("pending"))
        if total and (complete < total or pending):
            pending_keys.append((key, rec))

    total_videos = 0
    for key, rec in pending_keys:
        sm = (rec.get("note") or {}).get("media_summary") or {}
        total_videos += int(sm.get("videos_total", 0))
    done_videos = 0

    def write_prog(current):
        if not progress_file:
            return
        try:
            tmp = progress_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"total": total_videos, "done": done_videos,
                           "current": current}, f, ensure_ascii=False)
            os.replace(tmp, progress_file)
        except OSError:
            pass

    write_prog("")
    for key, rec in pending_keys:
        lid = rec.get("local_id")
        dbrow = conn.execute(
            f"""SELECT local_id, server_id, create_time, message_content, packed_info_data
                FROM {table} WHERE local_id=?""", (lid,)).fetchone()
        if not dbrow:
            continue
        lid2, server_id, ct, raw, packed = dbrow
        local_note_dir = find_note_dir(
            attach_root, md5table(CHAT)[4:], ct, rec.get("token"))
        if not local_note_dir or not any(
                os.path.isfile(x) for x in glob.glob(os.path.join(str(local_note_dir), "V", "*.mp4"))):
            continue
        title = ((rec.get("note") or {}).get("title") or str(lid))[:30]

        def tick(title=title):
            nonlocal done_videos
            done_videos += 1
            write_prog(title)

        prev_complete = (rec.get("note", {}).get("media_summary", {})
                         .get("videos_complete", 0))
        rebuild_note(conn, dbrow, out_dir, attach_root, media_root,
                     rec.get("type_code"), progress=tick)
        try:
            with open(notes_index_path, encoding="utf-8") as f:
                fresh_index = json.load(f)
            notes_index[key] = fresh_index.get(key, notes_index.get(key))
        except OSError:
            pass
        new_complete = (notes_index.get(key, {}).get("note", {})
                        .get("media_summary", {}).get("videos_complete", 0))
        if new_complete > prev_complete:
            updated += 1
            print(f"[补齐] 笔记 local_id={lid} 视频完成 {new_complete}，封面已刷新")
    if progress_file:
        try:
            os.remove(progress_file)
        except OSError:
            pass
    return updated


def run(dec_dir, out_dir, media_root, init_only=False, fast=False, backfill_only=False):
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(out_dir, "filehelper_state.json")
    jsonl_path = os.path.join(out_dir, "messages.jsonl")
    state = load_state(state_path)
    last_id = int(state.get("last_local_id", 0))

    conn = connect(os.path.join(dec_dir, MAIN_DB))
    table = md5table(CHAT)
    self_wxid = conn.execute("SELECT user_name FROM Name2Id WHERE rowid=1").fetchone()[0]
    max_id = conn.execute(f"SELECT MAX(local_id) FROM {table}").fetchone()[0] or 0

    if not state:
        # 首次运行：只建立基线，旧消息一条都不导出
        save_state(state_path, {"chat": CHAT, "last_local_id": max_id,
                                "initialized_at": int(time.time())})
        conn.close()
        print(f"[基线] 已把起点设为当前最新消息 local_id={max_id}，未导出任何旧消息。")
        return 0

    if init_only:
        conn.close()
        print(f"[基线] 保持现有起点 local_id={last_id}。")
        return 0

    rows = conn.execute(
        f"""SELECT local_id, server_id, real_sender_id, create_time, local_type,
                  message_content, packed_info_data FROM {table}
            WHERE local_id > ? ORDER BY create_time, local_id""", (last_id,)).fetchall()
    attach_root = os.path.join(media_root, "attach")
    # 每次运行也尝试补齐此前缺视频的笔记（视频在微信里下载后即可补齐）
    pf = os.environ.get("SYNC_PROGRESS_FILE")
    filled = 0 if fast else backfill_notes(conn, dec_dir, out_dir, attach_root,
                                           media_root, progress_file=pf)
    if backfill_only:
        conn.close()
        print(f"[补齐] 仅后台补齐视频，刷新了 {filled} 条笔记。")
        return 0
    if not rows:
        conn.close()
        print(("[无新增] 文件传输助手没有新消息。" +
               (f" 但自动补齐了 {filled} 条笔记的视频。" if filled else "")))
        return 0
    new_records = []
    for lid, server_id, sender_id, ct, mtype, raw, packed in rows:
        text = decode_content(raw)
        bt = base_type(mtype)
        ym = month_of(ct)
        is_self = (sender_id is not None and (
            (sender_id == 1) or
            (self_wxid and str(sender_id) != "0")))
        # filehelper 单聊：发送方不是“我”，就是文件传输助手
        who = "我" if is_self else CHAT_TITLE
        media = None
        note_ref = None
        body = text or ""
        md5 = packed_md5(packed)
        if bt == 3 and md5:
            rel = save_image(attach_root, md5, ym, out_dir)
            if rel:
                media = {"image_h": rel} if rel.endswith(("_h.jpg", "_h.png", "_h.webp")) else \
                        {"image_t": rel} if "_t." in rel else {"image": rel}
            body = ""
        elif bt == 43 and md5:
            vrel, trel = save_video(media_root, md5, ym, out_dir)
            if vrel or trel:
                media = {}
                if vrel:
                    media["video"] = vrel
                if trel:
                    media["image_t"] = trel
            body = ""
        elif bt == 49:
            if parse_record_xml(text or ""):
                dbrow = (lid, server_id, ct, raw, packed)
                note_ref, body = rebuild_note(
                    conn, dbrow, out_dir, attach_root, media_root, mtype,
                    skip_video=fast)
                media = None
            else:
                fields, body = app_title(text)
                fname = fields.get("filename")
                if fname and not fname.lower().startswith(("http://", "https://")):
                    frel = save_file(media_root, fname, ym, out_dir)
                    if frel:
                        media = {"file": frel, "file_fullname": frel}
                note_ref = None

        rec = {"chat": CHAT, "chat_title": CHAT_TITLE, "is_room": False,
               "sender_wxid": (self_wxid if is_self else CHAT), "sender": who,
               "is_self": bool(is_self), "time": fmt_time(ct), "create_time": ct,
               "type": type_label(mtype), "type_code": mtype, "local_id": lid,
               "server_id": server_id, "text": body, "media": media,
               "note_ref": note_ref if bt == 49 else None}
        new_records.append(rec)
    conn.close()

    with open(jsonl_path, "a", encoding="utf-8", newline="") as f:
        for r in new_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    state["last_local_id"] = max(last_id, max(r["local_id"] for r in new_records))
    state["updated_at"] = int(time.time())
    save_state(state_path, state)
    print(f"[完成] 新增 {len(new_records)} 条文件传输助手消息，已追加到 messages.jsonl")
    return len(new_records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dec_dir")
    ap.add_argument("out_dir")
    ap.add_argument("media_root")
    ap.add_argument("--wxid", help="微信原始 ID，用于解密图片缓存")
    ap.add_argument("--init-baseline", action="store_true",
                    help="只把起点设为当前最新消息，不导出旧消息")
    ap.add_argument("--storage", help="加密 db_storage 目录；提供后只解密主消息库")
    ap.add_argument("--key-json", help="含 db_key 的密钥结果文件(local/key_result.json)")
    ap.add_argument("--fast", action="store_true", help="快速入库：先存消息，跳过视频转码")
    ap.add_argument("--backfill-only", action="store_true", help="只后台补齐视频，不处理新消息")
    a = ap.parse_args()
    if a.wxid:
        note_cards.set_wxid(a.wxid)
    if a.storage and a.key_json:
        key_hex = json.load(open(a.key_json, encoding="utf-8"))["db_key"]
        decrypt_main_db(a.storage, key_hex, os.path.join(a.dec_dir, MAIN_DB))
    run(a.dec_dir, a.out_dir, a.media_root, init_only=a.init_baseline,
        fast=a.fast, backfill_only=a.backfill_only)


if __name__ == "__main__":
    main()
