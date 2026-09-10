# -*- coding: utf-8 -*-
"""Extract the local WeChat 4.x database key for your own Windows account.

Read-only memory inspection is used to obtain the key while WeChat is logged in.
The result is written only to the local path in your config file.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import pymem

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = ROOT / "tools" / "WeChatDataAnalysis"


def load_dependency(name):
    if not THIRD_PARTY.is_dir():
        raise SystemExit("缺少 tools/WeChatDataAnalysis，请按 README 先克隆第三方依赖。")
    sys.path.insert(0, str(THIRD_PARTY))
    return __import__(name)


def expand(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def default_dll():
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tencent" / "Weixin",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tencent" / "Weixin",
    ]
    found = [p for root in roots for p in root.glob("*/Weixin.dll") if p.is_file()]
    if not found:
        raise FileNotFoundError("找不到 Weixin.dll，请在 config.json 中配置 dll_path")
    return max(found, key=lambda p: p.stat().st_mtime)


def choose_probe(account_dir):
    storage = account_dir / "db_storage"
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "local" / "config.json"))
    parser.add_argument("--output")
    args = parser.parse_args()

    config_path = expand(args.config)
    config = json.load(open(config_path, encoding="utf-8"))
    account_dir = expand(config["account_dir"])
    output_path = expand(args.output or config.get("key_json") or ROOT / "local" / "key_result.json")
    dll_path = expand(config["dll_path"]) if config.get("dll_path") else default_dll()

    key_v4 = load_dependency("key_v4")
    dll_scan = load_dependency("dll_key_scan")
    probe = choose_probe(account_dir)
    if not probe:
        raise SystemExit("找不到可用数据库，请确认 account_dir 是否指向账号目录。")

    process = pymem.Pymem("Weixin.exe")
    print(f"[*] Weixin.exe PID={process.process_id}")
    print(f"[*] Weixin.dll: {dll_path}")
    masks = []
    for item in dll_scan.extract_xor_keys_from_dll(str(dll_path)):
        raw = bytes.fromhex(item["key_hex"])
        if len(raw) == 32 and raw not in masks:
            masks.append(raw)

    db_key = None
    internal_key = None
    for index, mask in enumerate(masks):
        print(f"[*] 使用 DLL 掩码候选 {index + 1}/{len(masks)}")
        key_v4.finish_flag = False
        raw = key_v4.recover_key(process.process_id, probe, mask)
        if raw:
            raw_key = bytes.fromhex(raw)
            db_key = bytes(a ^ b for a, b in zip(raw_key, mask)).hex()
            internal_key = mask.hex()
            break

    if not db_key:
        print("[*] 掩码提取失败，尝试无掩码扫描")
        key_v4.finish_flag = False
        db_key = key_v4.recover_key(process.process_id, probe)

    if not db_key:
        raise SystemExit("未能提取密钥：请确认微信已登录并保持解锁。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account": account_dir.name,
        "pid": process.process_id,
        "probe": probe,
        "db_key": db_key,
        "internal_key": internal_key,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[+] 密钥已保存: {output_path}")


if __name__ == "__main__":
    main()
