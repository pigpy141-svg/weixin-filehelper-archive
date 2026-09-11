# -*- coding: utf-8 -*-
"""微信资料库本地浏览器（仅标准库，仅监听 127.0.0.1，离线）。
用法: python viewer/server.py [archive_dir] [port]"""
import os, sys, json, html, time, re, mimetypes, threading, subprocess, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = Path(os.environ.get("WECHAT_ARCHIVE_CONFIG", os.path.join(ROOT, "local", "config.json")))
sys.path.insert(0, ROOT)
from exporter.lib import accounts as accmod


def configured_archive_dir():
    try:
        data = json.load(open(CONFIG_PATH, encoding="utf-8"))
        value = str(data.get("archive_dir") or "").strip()
        if not value:
            return None
        path = Path(os.path.expandvars(os.path.expanduser(value)))
        return str(path if path.is_absolute() else Path(ROOT) / path)
    except Exception:
        return None

DEFAULT_ARCHIVE_DIR = os.path.join(ROOT, "local", "archive")
ARCH = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                       (configured_archive_dir() or DEFAULT_ARCHIVE_DIR))
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
ACCOUNT_ROOT = os.path.join(ARCH, "accounts")
LEGACY_CHAT = "filehelper"
CHAT_TITLE = "文件传输助手"


def account_dirs():
    root = ARCH
    if os.path.isdir(ACCOUNT_ROOT):
        dirs = [p for p in Path(ACCOUNT_ROOT).iterdir() if p.is_dir()]
    else:
        dirs = [Path(root)]
    result = []
    for d in sorted(dirs, key=lambda x: x.name.lower()):
        if (d / "messages.jsonl").is_file():
            result.append(d)
    legacy = Path(root) / "messages.jsonl"
    if legacy.is_file() and legacy.parent not in result:
        result.append(legacy.parent)
    return result


def slug_of_dir(path):
    path = Path(path)
    return path.name if Path(ACCOUNT_ROOT) in path.parents else "legacy"


def chat_of_slug(slug):
    return f"filehelper@{slug}"


def slug_of_chat(chat):
    if not chat:
        return None
    return chat.split("@", 1)[1] if chat.startswith("filehelper@") else None


def load_archive_dir(d):
    recs = []
    messages_path = os.path.join(d, "messages.jsonl")
    if os.path.exists(messages_path):
        with open(messages_path, encoding="utf-8") as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    fav = []
    fp = os.path.join(d, "favorites.json")
    if os.path.exists(fp):
        try:
            fav = json.load(open(fp, encoding="utf-8"))
        except Exception:
            fav = []
    notes = {}
    notes_path = os.path.join(d, "notes_index.json")
    if os.path.exists(notes_path):
        try:
            notes = json.load(open(notes_path, encoding="utf-8"))
        except Exception:
            notes = {}
    return recs, fav, notes


def load_all():
    all_recs, all_fav, convs, note_map = [], [], [], {}
    for d in account_dirs():
        recs, fav, notes = load_archive_dir(d)
        slug = slug_of_dir(d)
        chat = chat_of_slug(slug)
        title_prefix = "" if slug == "legacy" else f"[{slug}] "
        if recs:
            times = [r.get("create_time") or 0 for r in recs]
            convs.append({"chat": chat, "slug": slug, "dir": str(d),
                          "title": title_prefix + CHAT_TITLE, "is_room": False,
                          "count": len(recs), "first": min(times), "last": max(times)})
        for r in recs:
            r["account"] = slug
            r["chat"] = chat
            r["chat_title"] = title_prefix + CHAT_TITLE
            m = r.get("media") or {}
            if isinstance(m, dict):
                for k, v in list(m.items()):
                    if isinstance(v, str) and v and "://" not in v:
                        m[k] = os.path.join("accounts", slug, v).replace("\\", "/")
                r["media"] = m
            if r.get("note_ref"):
                r["note_ref"] = os.path.join("accounts", slug, r["note_ref"]).replace("\\", "/")
            all_recs.append(r)
        for key, note in notes.items():
            note = dict(note)
            rel_dir = note.get("dir") or ""
            if rel_dir:
                note["dir"] = os.path.join("accounts", slug, rel_dir).replace("\\", "/")
            note["account"] = slug
            note_map[(chat, note.get("local_id"))] = note
        for item in fav:
            item["account"] = slug
            all_fav.append(item)
    convs.sort(key=lambda x: x.get("last", 0), reverse=True)
    all_recs.sort(key=lambda r: (r.get("create_time") or 0, r.get("local_id") or 0))
    return convs, all_recs, all_fav, note_map


INDEX, RECS, FAV, NOTES_BY_MSG = load_all()
NOTES = NOTES_BY_MSG

PROGRESS_DIR = os.path.join(ROOT, "local", "progress")
PROGRESS_FILE = os.path.join(PROGRESS_DIR, "viewer.json")
BG_STATE = {"running": False, "done": False, "updated": 0, "output": "",
            "started": 0, "returncode": None}
_NO_WIN = 0x08000000 if os.name == "nt" else 0
_SP_KW = {"creationflags": _NO_WIN} if os.name == "nt" else {}


def reload_data():
    global INDEX, RECS, FAV, NOTES, NOTES_BY_MSG
    INDEX, RECS, FAV, NOTES_BY_MSG = load_all()
    NOTES = NOTES_BY_MSG


def load_config():
    return accmod.load_config(CONFIG_PATH)


def _run_capture(argv, env, timeout=None):
    p = subprocess.run(argv, cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, **_SP_KW)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def _maybe_refresh_key(env):
    extractor = os.path.join(ROOT, "tools", "extract_key.py")
    if not os.path.isfile(extractor):
        return False
    r = subprocess.run([sys.executable, "-u", extractor, "--config", str(CONFIG_PATH),
                        "--active-only"],
                       cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=180, **_SP_KW)
    return r.returncode == 0


def _transcode_workers():
    return max(2, min(4, (os.cpu_count() or 4) // 2 or 2))


def read_progress():
    data = {}
    if os.path.isdir(PROGRESS_DIR):
        for p in Path(PROGRESS_DIR).glob("*.json"):
            try:
                data[p.stem] = json.load(open(p, encoding="utf-8"))
            except Exception:
                pass
    return data


def _start_backfill(env):
    if BG_STATE["running"]:
        return
    BG_STATE.update(running=True, done=False, updated=0, output="",
                    started=int(time.time()), returncode=None)

    def worker():
        argv = [sys.executable, "-u", os.path.join(ROOT, "exporter", "sync_accounts.py"),
                "--config", str(CONFIG_PATH), "--active-only",
                "--backfill-only"]
        try:
            r = subprocess.run(argv, cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **_SP_KW)
            reload_data()
            BG_STATE.update(running=False, done=True,
                            output=r.stdout.decode("utf-8", "replace")[-2000:],
                            returncode=r.returncode)
        except Exception as ex:
            BG_STATE.update(running=False, done=True, output=str(ex), returncode=1)

    threading.Thread(target=worker, daemon=True).start()


def run_sync():
    config = load_config()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    before = len(RECS)
    result_file = os.path.join(PROGRESS_DIR, "last_sync.json")
    argv = [sys.executable, "-u", os.path.join(ROOT, "exporter", "sync_accounts.py"),
            "--config", str(CONFIG_PATH), "--active-only",
            "--json-result", result_file]
    os.makedirs(PROGRESS_DIR, exist_ok=True)
    code, out = _run_capture(argv, env, timeout=300)
    if code != 0:
        _maybe_refresh_key(env)
        code, out = _run_capture(argv, env, timeout=300)
    reload_data()
    added = max(0, len(RECS) - before)
    _start_backfill(env)
    return {"ok": code == 0, "added": added, "total": len(RECS),
            "pending_videos": True, "output": out[-2000:]}

def tags_path():
    return os.path.join(ARCH, "tags.json")

def load_tags():
    p = tags_path()
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            return d.get("tags", []), d.get("membership", {})
        except Exception:
            return [], {}
    return [], {}

TAGS, MEMBERSHIP = load_tags()

def save_tags():
    p = tags_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tags": TAGS, "membership": MEMBERSHIP}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)

def item_key(r):
    return str(r.get("chat")) + "::" + str(r.get("local_id"))

def tag_public():
    counts = {}
    for ids in MEMBERSHIP.values():
        for i in set(ids):
            counts[i] = counts.get(i, 0) + 1
    alive = {t["id"] for t in TAGS}
    return [dict(t, count=counts.get(t["id"], 0)) for t in TAGS if t["id"] in alive]

def render_note(record):
    note = record.get("note") or {}
    note_dir = record.get("dir") or ""
    summary = note.get("media_summary") or {}
    items = note.get("items", [])

    title = (note.get("title") or "").strip()
    snippet = ""
    if not title:
        for item in items:
            if item.get("kind") == "text" and item.get("text"):
                snippet = item["text"].strip().splitlines()[0]
                break
    heading = esc(title or snippet or "微信笔记")

    meta = []
    if summary.get("pending"):
        meta.append("\u23f3 \u89c6\u9891\u540e\u53f0\u5904\u7406\u4e2d\uff0c\u7a0d\u540e\u70b9\u540c\u6b65\u5237\u65b0")
    elif summary.get("videos_total") is not None:
        meta.append(f'{summary.get("videos_complete", 0)}/{summary.get("videos_total", 0)} 个视频已本地保存')
    if summary.get("html"):
        meta.append("含原始 HTML")
    meta_html = '<div class="note-meta">' + esc(" · ".join(meta)) + "</div>" if meta else ""

    parts = [
        '<div class="note-card collapsed">',
        '<div class="note-head" onclick="toggleNote(this)">',
        '<span class="note-caret">▶</span>',
        '<div class="note-head-main">',
        '<div class="note-title-row"><span class="note-title">📝 微信笔记</span>',
        '<span class="note-hint">点击展开</span></div>',
        '<div class="note-sub">' + heading + '</div>',
        meta_html,
        '</div>',
        '<button type="button" class="note-collapse" onclick="event.stopPropagation();toggleNote(this)">收起 ▲</button>',
        '</div>',
        '<div class="note-body">',
    ]
    if title:
        parts.append(f'<div class="note-heading">{esc(title)}</div>')
    for item in items:
        kind = item.get("kind")
        if kind == "text" and item.get("text"):
            parts.append('<div class="note-text">' + esc(item["text"]).replace("\n", "<br>") + "</div>")
        elif kind == "video":
            label = esc(item.get("htmlid") or "视频")
            duration = f'{item["duration"]} 秒' if item.get("duration") else "视频"
            parts.append(f'<div class="note-video-title">🎬 {label} · {esc(duration)}</div>')
            video = note_media_url(note_dir, item.get("video"))
            thumb = note_media_url(note_dir, item.get("thumb"))
            if video and item.get("video_complete"):
                poster = f' poster="{thumb}"' if thumb else ""
                parts.append(f'<video controls preload="none"{poster}><source src="{video}" type="video/mp4"></video>')
            elif thumb:
                parts.append(f'<a href="{thumb}" target="_blank"><img loading="lazy" class="thumb" src="{thumb}"></a><div class="ph">视频本地未完整缓存（封面已保存）</div>')
            else:
                parts.append('<div class="ph">视频本地未缓存</div>')
        elif kind == "html" and item.get("html"):
            url = note_media_url(note_dir, item.get("html"))
            parts.append(f'<a class="filelink" href="{url}" target="_blank">📄 打开微信原始 HTML</a>')
    if note.get("url"):
        parts.append('<div class="note-link">卡片链接：<a href="' + esc(note["url"]) + '" target="_blank">' + esc(note["url"]) + "</a></div>")
    parts.append("</div></div>")
    return "".join(parts)

def render_msg(r):
    bt = r.get("type_code", 0)
    bt = bt & 0xFFFF if isinstance(bt, int) else bt
    who = esc(r.get("sender"))
    self_cls = " self" if r.get("is_self") else ""
    ts = esc(r.get("time"))
    m = r.get("media") or {}
    body = ""
    text = r.get("text") or ""
    if bt == 1:
        body = esc(text).replace("\n", "<br>")
    elif bt == 3:
        u = media_url(m, "image_h") or media_url(m, "image") or media_url(m, "image_t")
        body = f'<a href="{u}" target="_blank"><img loading="lazy" src="{u}"></a>' if u else '<span class="ph">[图片(本地未缓存)]</span>'
    elif bt == 43:
        v = media_url(m, "video")
        t = media_url(m, "image_t") or media_url(m, "image_h")
        if v:
            body = f'<video controls preload="none" poster="{t or ""}"><source src="{v}" type="video/mp4"></video>'
        elif t:
            body = f'<a href="{t}" target="_blank"><img loading="lazy" class="thumb" src="{t}"></a><div class="ph">[视频(仅缩略图)]</div>'
        else:
            body = '<span class="ph">[视频(本地未缓存)]</span>'
    elif bt == 34:
        body = '<span class="ph">[语音]</span>'
    elif bt == 49:
        note_record = None
        if r.get("note_ref"):
            note_record = NOTES_BY_MSG.get((r.get("chat"), r.get("local_id")))
            if not note_record or note_record.get("dir") != r.get("note_ref"):
                note_record = next((x for x in NOTES.values() if x.get("dir") == r.get("note_ref")), None)
        if note_record:
            body = render_note(note_record)
        else:
            fp = m.get("file") or m.get("file_fullname")
            if fp:
                body = f'<a class="filelink" href="#" data-open="{esc(fp)}">📄 {esc(os.path.basename(fp))}</a> <a class="fdllink" href="/media/{quote(fp)}" download>⬇ 下载</a>'
            else:
                body = '<span class="ph">' + esc(text) + "</span>" if text else '<span class="ph">[链接/文件]</span>'
    elif bt in (10000, 10002):
        body = f'<span class="sys">{esc(text)}</span>'
    elif bt == 47:
        body = '<span class="ph">[表情]</span>'
    elif bt == 42:
        body = '<span class="ph">[个人名片]</span>'
    elif bt == 48:
        body = '<span class="ph">[位置] ' + esc(text) + "</span>"
    elif bt == 50:
        body = '<span class="ph">[语音/视频通话]</span>'
    else:
        body = '<span class="ph">[' + esc(r.get("type")) + "] " + esc(text) + "</span>"
    return f'<div class="msg{self_cls}"><div class="meta"><span class="who">{who}</span><span class="ts">{ts}</span></div><div class="bubble">{body}</div></div>'

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, ctype, data, extra=None):
        if isinstance(data, str): data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False))

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            body = {}
        if u.path == "/api/sync":
            try:
                res = run_sync()
            except Exception as ex:
                res = {"ok": False, "error": str(ex)}
            return self._json(res)
        if u.path == "/api/tags":
            name = (body.get("name") or "").strip()
            if not name:
                return self._json({"ok": False, "error": "名称不能为空"}, 400)
            if len(name) > 24:
                name = name[:24]
            for t in TAGS:
                if t["name"] == name:
                    return self._json({"ok": True, "tag": t})
            rec = {"id": "t_" + uuid.uuid4().hex[:10],
                   "name": name, "created": int(time.time())}
            TAGS.append(rec)
            TAGS.sort(key=lambda t: t.get("created", 0))
            save_tags()
            return self._json({"ok": True, "tag": rec})
        if u.path == "/api/tags/assign":
            items = [str(x) for x in body.get("items", [])]
            add = [str(x) for x in body.get("add", [])]
            remove = [str(x) for x in body.get("remove", [])]
            alive = {t["id"] for t in TAGS}
            add = [x for x in add if x in alive]
            for k in items:
                cur = set(MEMBERSHIP.get(k, []))
                cur.update(add)
                cur.difference_update(remove)
                if cur:
                    MEMBERSHIP[k] = sorted(cur)
                elif k in MEMBERSHIP:
                    del MEMBERSHIP[k]
            save_tags()
            return self._json({"ok": True})
        if u.path == "/api/tags/delete":
            tid = str(body.get("id") or "")
            if not any(t["id"] == tid for t in TAGS):
                return self._json({"ok": False, "error": "标签不存在"}, 404)
            TAGS[:] = [t for t in TAGS if t["id"] != tid]
            for k in list(MEMBERSHIP.keys()):
                rest = [x for x in MEMBERSHIP[k] if x != tid]
                if rest:
                    MEMBERSHIP[k] = rest
                else:
                    del MEMBERSHIP[k]
            save_tags()
            return self._json({"ok": True})
        if u.path == "/api/tags/rename":
            tid = str(body.get("id") or "")
            name = (body.get("name") or "").strip()[:24]
            t = next((x for x in TAGS if x["id"] == tid), None)
            if not t:
                return self._json({"ok": False, "error": "标签不存在"}, 404)
            if not name:
                return self._json({"ok": False, "error": "名称不能为空"}, 400)
            if any(x["name"] == name and x["id"] != tid for x in TAGS):
                return self._json({"ok": False, "error": "名称已存在"}, 400)
            t["name"] = name
            save_tags()
            return self._json({"ok": True, "tag": t})
        self._json({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE)
        elif u.path == "/api/convs":
            arr = [{"chat": e["chat"], "title": e["title"], "is_room": e["is_room"],
                    "count": e["count"], "last": fmt_ts(e["last"]),
                    "last_ts": e["last"]} for e in INDEX]
            self._send(200, "application/json; charset=utf-8", json.dumps(arr, ensure_ascii=False))
        elif u.path == "/api/messages":
            chat = q.get("chat", [""])[0]
            msgs = [r for r in RECS if r.get("chat") == chat]
            self._send(200, "application/json; charset=utf-8",
                       json.dumps([dict(r, html=render_msg(r)) for r in msgs], ensure_ascii=False))
        elif u.path == "/api/folders":
            chat = q.get("chat", [""])[0]
            tagf = q.get("tag", [""])[0]
            groups = {"notes": [], "files": [], "images": [], "videos": [],
                      "texts": [], "others": []}
            for r in RECS:
                if r.get("chat") != chat:
                    continue
                t = r.get("type_code", 0)
                bt = t & 0xFFFF if isinstance(t, int) else t
                m = r.get("media") or {}
                if r.get("note_ref"):
                    key = "notes"
                elif bt == 3:
                    key = "images"
                elif bt == 43:
                    key = "videos"
                elif bt == 49 and (m.get("file") or m.get("file_fullname")):
                    key = "files"
                elif bt == 1:
                    key = "texts"
                else:
                    key = "others"
                groups[key].append(dict(r, html=render_msg(r)))
            if tagf:
                for k in list(groups.keys()):
                    groups[k] = [r for r in groups[k]
                                 if tagf in MEMBERSHIP.get(item_key(r), [])]
            out = []
            labels = {"notes": "\U0001f4dd 微信笔记", "files": "\U0001f4c4 文件",
                      "images": "\U0001f5bc\ufe0f 图片", "videos": "\U0001f3ac 视频",
                      "texts": "\U0001f4ac 文本", "others": "\U0001f4ce 其他"}
            for key, ms in groups.items():
                if not ms:
                    continue
                out.append({"key": key, "label": labels[key], "count": len(ms),
                            "first": min(x.get("create_time") or 0 for x in ms),
                            "last": max(x.get("create_time") or 0 for x in ms),
                            "items": ms})
            out.sort(key=lambda g: g["last"], reverse=True)
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(out, ensure_ascii=False))
        elif u.path == "/api/sync/status":
            st = dict(BG_STATE)
            st["progress"] = read_progress()
            st["workers"] = _transcode_workers()
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(st, ensure_ascii=False))
        elif u.path == "/api/tags":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"tags": tag_public(), "membership": MEMBERSHIP},
                                  ensure_ascii=False))
        elif u.path == "/api/search":
            kw = q.get("q", [""])[0].lower().strip()
            scope = q.get("chat", [""])[0]
            lim = int(q.get("limit", ["300"])[0])
            out = []
            for r in RECS:
                if scope and r.get("chat") != scope: continue
                t = (r.get("text") or "").lower()
                if kw and kw not in t: continue
                out.append({"chat": r.get("chat"), "title": r.get("chat_title"),
                            "time": r.get("time"), "sender": r.get("sender"),
                            "type": r.get("type"), "snippet": (r.get("text") or "")[:300]})
                if len(out) >= lim: break
            self._send(200, "application/json; charset=utf-8", json.dumps(out, ensure_ascii=False))
        elif u.path == "/api/favorites":
            kw = q.get("q", [""])[0].lower().strip()
            out = []
            for x in FAV:
                blob = " ".join(str(x.get(k) or "") for k in ("title","desc","link","content_text","fromusr","type")).lower()
                if kw and kw not in blob: continue
                out.append(x)
            self._send(200, "application/json; charset=utf-8", json.dumps(out[:1000], ensure_ascii=False))
        elif u.path == "/api/open":
            rel = q.get("path", [""])[0]
            base = os.path.abspath(ARCH)
            p = os.path.abspath(os.path.join(base, rel))
            if p != base and not p.startswith(base + os.sep):
                res = {"ok": False, "error": "forbidden"}
            elif not os.path.isfile(p):
                res = {"ok": False, "error": "file not found"}
            else:
                try:
                    if sys.platform == "win32":
                        os.startfile(p)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", p])
                    else:
                        subprocess.Popen(["xdg-open", p])
                    res = {"ok": True}
                except Exception as ex:
                    res = {"ok": False, "error": str(ex)}
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(res, ensure_ascii=False))
        elif u.path.startswith("/media/"):
            rel = unquote(u.path[len("/media/"):])
            p = os.path.abspath(os.path.join(ARCH, rel))
            if not p.startswith(os.path.abspath(ARCH)):
                return self._send(403, "text/plain", "forbidden")
            if os.path.isfile(p):
                ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
                data = open(p, "rb").read()
                self._send(200, ct, data)
            else:
                self._send(404, "text/plain", "not found: " + rel)
        else:
            self._send(404, "text/plain", "not found")

PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html"),
            encoding="utf-8").read()

if __name__ == "__main__":
    s = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"资料库浏览器: http://127.0.0.1:{PORT}  (Ctrl+C 退出)")
    print("目录:", ARCH)
    s.serve_forever()
