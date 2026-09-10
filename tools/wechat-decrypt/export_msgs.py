# -*- coding: utf-8 -*-
"""
微信 4.x 消息导出示例（含逐库 sender 验证）
⚠️ 关键坑: 每个 message_*.db 的 real_sender_id 语义可能不同!
   必须用"锚点句"逐库验证映射后再导出。

用法: python export_msgs.py <decrypted_db_dir> <target_table> <output.txt>
示例: python export_msgs.py decrypted Msg_<表名hash> out.txt
"""
import sqlite3, os, sys, time, re
from collections import Counter

# 锚点句: (句子片段, 说话者) - 用你确认过归属的消息作为示例
# ⚠️ 请替换为你们自己确认过的句子（此处仅作格式示例）
ANCHORS = [
    ("某句用户说过的话", "用户"),
    ("某句对方说过的话", "对方"),
]

def find_mapping(db_path, table):
    """用锚点句验证该库的 sender 映射"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    mapping = {}  # sender -> Counter
    for kw, who in ANCHORS:
        try:
            cur.execute("SELECT real_sender_id, COUNT(*) FROM %s WHERE message_content LIKE ? GROUP BY real_sender_id" % table, ("%" + kw + "%",))
            for sid, cnt in cur.fetchall():
                mapping.setdefault(sid, Counter())[who] += cnt
        except Exception:
            pass
    conn.close()
    result = {}
    for sid in mapping:
        cnt = mapping[sid]
        # 归属判断
        if cnt.get("用户", 0) > cnt.get("对方", 0):
            result[sid] = "用户"
        elif cnt.get("对方", 0) > 0:
            result[sid] = "对方"
        else:
            result[sid] = "未知"
    return result

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return
    db_dir, table, out_file = sys.argv[1], sys.argv[2], sys.argv[3]

    all_msgs = []
    for f in sorted(os.listdir(db_dir)):
        if not f.startswith("message_message_") or not f.endswith(".db"):
            continue
        db_path = os.path.join(db_dir, f)
        mapping = find_mapping(db_path, table)
        if not mapping:
            print("[SKIP] %s 无锚点命中, 跳过(需补充锚点句)" % f)
            continue
        print("[%s] sender映射: %s" % (f, mapping))
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT real_sender_id, create_time, message_content, local_type FROM %s" % table)
        for sid, ct, content, mtype in cur.fetchall():
            who = mapping.get(sid, "系统(%s)" % sid)
            text = str(content) if mtype == 1 else ""
            if text and not text.startswith("b'"):
                all_msgs.append((ct, who, text))
        conn.close()

    all_msgs.sort(key=lambda x: x[0] or 0)
    with open(out_file, "w", encoding="utf-8") as f:
        for ct, who, text in all_msgs:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ct)) if ct else "?"
            f.write("[%s] %s: %s\n" % (ts, who, text))
    print("导出 %d 条 -> %s" % (len(all_msgs), out_file))

if __name__ == "__main__":
    main()
