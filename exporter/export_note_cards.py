# -*- coding: utf-8 -*-
"""扫描消息库中的微信笔记卡片，复制本地视频/封面/HTML 到 archive/notes。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wxcommon import connect, decode_content, base_type, md5table
from note_cards import (parse_record_xml, packed_token, find_note_dir,
                        attach_note_media, note_dir_name, message_key)

DATABASES = ("message_message_0.db", "message_biz_message_0.db")


def load_name2id(conn):
    return {row_id: name for row_id, name in conn.execute("SELECT rowid,user_name FROM Name2Id")}


def note_markdown(note, record):
    lines = [f"# {note.get('title') or '微信笔记'}", "",
             f"- 来源会话: `{record['chat']}`",
             f"- 消息 ID: {record['local_id']}",
             f"- 消息时间: {record['time']}",
             f"- 视频: {note['media_summary']['videos_complete']} 个完整 / "
             f"{note['media_summary']['videos_partial']} 个未下完，共 {note['video_count']} 个",
             ""]
    if note.get("url"):
        lines.extend([f"- 卡片链接: {note['url']}", ""])
    for item in note["items"]:
        kind = item.get("kind")
        if kind == "text":
            lines.extend([item.get("text", ""), ""])
        elif kind == "video":
            title = item.get("htmlid") or "视频"
            duration = item.get("duration")
            size = item.get("datasize")
            suffix = []
            if duration:
                suffix.append(f"{duration} 秒")
            if size:
                suffix.append(f"原始大小 {size:,} 字节")
            meta = f"（{'，'.join(suffix)}）" if suffix else ""
            lines.append(f"## {title}{meta}")
            if item.get("thumb"):
                lines.append(f"![封面]({item['thumb']})")
            if item.get("video"):
                label = "视频未完整缓存（partial，可能无法播放）" if not item.get("video_complete") else "播放视频"
                lines.append(f"🎬 [{label}]({item['video']})")
            else:
                lines.append("🎬 视频本地未缓存")
            lines.append("")
        elif kind == "html":
            if item.get("html"):
                lines.extend([f"📄 [微信原始 HTML]({item['html']})", ""])
    return "\n".join(lines).strip() + "\n"


def export_database(db_path, attach_root, notes_dir, records):
    conn = connect(db_path)
    id2name = load_name2id(conn)
    name_by_table = {md5table(name): name for name in set(id2name.values())}
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg\\_%' ESCAPE '\\'")]
    found = copied = 0
    for table in tables:
        chat = name_by_table.get(table)
        if not chat:
            continue
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if "message_content" not in cols:
            continue
        query = f"""SELECT local_id,server_id,real_sender_id,create_time,local_type,
                           message_content,packed_info_data FROM {table}
                    ORDER BY create_time,local_id"""
        for local_id, server_id, sender_id, create_time, msg_type, raw, packed in conn.execute(query):
            if base_type(msg_type) != 49:
                continue
            text = decode_content(raw)
            note = parse_record_xml(text or "")
            if not note:
                continue
            token = packed_token(decode_content(packed) if isinstance(packed, bytes) else packed)
            folder = note_dir_name(chat, local_id, token)
            target_dir = os.path.join(notes_dir, folder)
            os.makedirs(target_dir, exist_ok=True)
            chat_hash = md5table(chat)[4:]
            local_note_dir = find_note_dir(attach_root, chat_hash, create_time, token)
            if local_note_dir:
                note["local_cache"] = os.path.relpath(str(local_note_dir), attach_root)
                attach_note_media(note, local_note_dir, target_dir)
            else:
                note["local_cache"] = None
                note["media_summary"] = {
                    "videos_total": note["video_count"], "videos_complete": 0,
                    "videos_partial": 0, "thumbs": 0,
                    "html": any(item.get("kind") == "html" for item in note["items"]),
                }
            record = {
                "chat": chat,
                "local_id": local_id,
                "server_id": server_id,
                "sender_id": sender_id,
                "create_time": create_time,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(create_time))),
                "type_code": msg_type,
                "token": token,
                "dir": os.path.relpath(target_dir, os.path.dirname(notes_dir)).replace("\\", "/"),
                "note": note,
            }
            with open(os.path.join(target_dir, "note.json"), "w", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
            with open(os.path.join(target_dir, "note.md"), "w", encoding="utf-8") as output:
                output.write(note_markdown(note, record))
            records[message_key(chat, local_id)] = record
            found += 1
            if note["media_summary"]["videos_complete"]:
                copied += 1
    conn.close()
    return found, copied


def main(dec_dir, out_dir, attach_root):
    notes_dir = os.path.join(out_dir, "notes")
    os.makedirs(notes_dir, exist_ok=True)
    records = {}
    for filename in DATABASES:
        db_path = os.path.join(dec_dir, filename)
        if os.path.exists(db_path):
            found, copied = export_database(db_path, attach_root, notes_dir, records)
            print(f"[+] {filename}: 笔记 {found} 条，含完整本地视频的 {copied} 条")
    index_path = os.path.join(out_dir, "notes_index.json")
    with open(index_path, "w", encoding="utf-8") as output:
        json.dump(records, output, ensure_ascii=False, indent=2)
    videos = sum(item["note"]["media_summary"]["videos_complete"] for item in records.values())
    partials = sum(item["note"]["media_summary"]["videos_partial"] for item in records.values())
    thumbs = sum(item["note"]["media_summary"]["thumbs"] for item in records.values())
    print(f"[+] 合计笔记 {len(records)} 条；完整视频 {videos}，未下完 {partials}，封面 {thumbs}")
    print(f"[+] 索引: {index_path}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("用法: python export_note_cards.py <decrypted_db_dir> <archive_dir> <wechat_attach_dir>")
    main(sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3]))
