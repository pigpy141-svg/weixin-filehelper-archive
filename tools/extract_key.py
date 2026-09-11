# -*- coding: utf-8 -*-
"""提取本机当前登录微信 4.x 的数据库密钥。

默认扫描 xwechat_files 下的全部账号，并把密钥按账号分别写入：
local/keys/<wxid>.json

切换或登录任意微信号后重新运行本脚本即可，不依赖固定 config.json。
只读检查微信进程内存，密钥仅保存在本机 local 目录。
"""
import argparse
import ctypes
import datetime
import importlib
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

import pymem

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exporter.lib import accounts as acc

# 第三方 root 版 key_v4.py 使用 ctypes.LPVOID；标准库中该名称位于 ctypes.wintypes。
if not hasattr(ctypes, "LPVOID"):
    ctypes.LPVOID = wintypes.LPVOID

THIRD_PARTY = ROOT / "tools" / "WeChatDataAnalysis"


def load_dependency(name):
    if not THIRD_PARTY.is_dir():
        raise SystemExit("缺少 tools/WeChatDataAnalysis，请重新运行 scripts/setup.ps1。")
    candidates = []
    for base in (THIRD_PARTY / "src", THIRD_PARTY):
        if base.is_dir() and str(base) not in sys.path:
            candidates.append(base)
    sys.path[:0] = [str(p) for p in candidates]
    return importlib.import_module(name)


def expand(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def default_dll():
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tencent" / "Weixin",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tencent" / "Weixin",
    ]
    found = [p for root in roots for p in root.glob("*/Weixin.dll") if p.is_file()]
    found.extend(p for root in roots for p in root.glob("Weixin.dll") if p.is_file())
    if not found:
        raise FileNotFoundError("找不到 Weixin.dll，可在 config.json 中配置 dll_path")
    return max(found, key=lambda p: p.stat().st_mtime)


def choose_probe(account):
    storage = account["storage"]
    priority = ["msg0.db", "msg.db", "micromsg.db", "favorite.db",
                "mediamsg0.db", "media_msg0.db", "sns.db", "general.db"]
    dbs = [p for p in storage.rglob("*.db")
           if p.name.lower() != "key_info.db" and p.stat().st_size >= 4096]

    def rank(path):
        try:
            return priority.index(path.name.lower())
        except ValueError:
            return len(priority)

    dbs.sort(key=lambda p: (rank(p), len(p.parts), str(p).lower()))
    return str(dbs[0]) if dbs else None


def weixin_pids():
    try:
        import psutil
        pids = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if (proc.info.get("name") or "").lower() != "weixin.exe":
                    continue
                cmd = " ".join(proc.info.get("cmdline") or [])
                pids.append((len(cmd), proc.info["pid"]))
            except Exception:
                pass
        return [pid for _, pid in sorted(pids)]
    except Exception:
        return [pymem.Pymem("Weixin.exe").process_id]


def attempt_scan(key_v4, pid, probe, masks):
    internal_key = None
    raw_hex = None
    for index, mask in enumerate(masks):
        print(f"    DLL 掩码候选 {index + 1}/{len(masks)}")
        key_v4.finish_flag = False
        raw_hex = key_v4.recover_key(pid, probe, mask)
        if raw_hex:
            raw_key = bytes.fromhex(raw_hex)
            return (bytes(a ^ b for a, b in zip(raw_key, mask))).hex(), mask.hex()
    print("    掩码未命中，尝试无掩码扫描")
    key_v4.finish_flag = False
    raw_hex = key_v4.recover_key(pid, probe)
    return (raw_hex, internal_key) if raw_hex else (None, None)


def save_key(account, db_key, internal_key, pid, probe, output=None):
    output = Path(output) if output else acc.key_path(account)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account": account["dir"].name,
        "wxid": account["wxid"],
        "pid": pid,
        "probe": probe,
        "db_key": db_key,
        "internal_key": internal_key,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    tmp = output.with_suffix(output.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, output)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "local" / "config.json"))
    parser.add_argument("--output", help="旧配置兼容：固定写入一个密钥文件")
    parser.add_argument("--wxid", help="只提取指定 wxid（不带尾部账号后缀）")
    parser.add_argument("--all", action="store_true", help="扫描所有发现的账号目录")
    parser.add_argument("--active-only", action="store_true",
                        help="只扫描近期有数据库写入的账号（默认）")
    args = parser.parse_args()

    config = acc.load_config(args.config) if Path(args.config).is_file() else {}
    dll_path = expand(config["dll_path"]) if config.get("dll_path") else default_dll()
    targets = acc.discover_accounts(config)
    if not targets:
        raise SystemExit("找不到微信账号目录，请确认微信已登录过或在 config.json 配置 wechat_files_dir。")

    if args.wxid:
        targets = [a for a in targets if a['wxid'] == args.wxid]
    elif not args.all and config.get('account_dir') and not args.active_only:
        configured = expand(config['account_dir'])
        configured_wxid = acc.wxid_from_dir(configured)
        if configured_wxid:
            targets = [a for a in targets if a['wxid'] == configured_wxid]
        if not targets:
            print('[!] config.json 指向的账号目录当前不存在，改为自动发现账号。')
            targets = acc.active_accounts(config)
    else:
        targets = acc.active_accounts(config)

    if not targets:
        raise SystemExit("没有符合条件的微信账号。")

    key_v4 = load_dependency("key_v4")
    dll_scan = load_dependency("wechat_decrypt_tool.dll_key_scan")
    masks = []
    for item in dll_scan.extract_xor_keys_from_dll(str(dll_path)):
        raw = bytes.fromhex(item["key_hex"])
        if len(raw) == 32 and raw not in masks:
            masks.append(raw)

    pids = weixin_pids()
    if not pids:
        raise SystemExit("未找到 Weixin.exe，请先登录并解锁微信。")
    print(f"[*] Weixin.dll: {dll_path}")
    print(f"[*] 微信进程 PID: {', '.join(map(str, pids))}")

    success = []
    for account in targets:
        print(f"[*] 账号 {account['wxid']}：{account['dir']}")
        if acc.account_has_valid_key(account, config) and not args.output:
            print("    已有可解密当前数据库的密钥，跳过。")
            success.append((account, acc.key_path(account, config)))
            continue
        probe = choose_probe(account)
        if not probe:
            print("    找不到可用探测数据库，跳过。")
            continue
        found = None
        for pid in pids:
            print(f"    检查进程 PID={pid}")
            try:
                pymem.Pymem().open_process_from_id(pid)
                db_key, internal_key = attempt_scan(key_v4, pid, probe, masks)
            except Exception as exc:
                print(f"    进程不可读：{exc}")
                continue
            if not db_key:
                continue
            if acc.key_matches_account(db_key, account):
                found = (db_key, internal_key, pid, probe)
                break
            print("    读到候选密钥但 HMAC 不匹配，继续尝试其它进程。")
        if not found:
            print("    未能提取该账号密钥：请确认微信已登录、解锁并打开过文件传输助手。")
            continue
        db_key, internal_key, pid, probe = found
        out = save_key(account, db_key, internal_key, pid, probe,
                       args.output if args.output else None)
        print(f"[+] 密钥已保存: {out}")
        success.append((account, out))

    if not success:
        raise SystemExit("未能提取任何账号密钥：请确认微信已登录并保持解锁；微信 4.1.13 若所有进程均提示不可读，请用管理员身份运行 PowerShell 后重试。")
    print(f"[完成] 可用账号密钥 {len(success)}/{len(targets)}。")


if __name__ == "__main__":
    main()
