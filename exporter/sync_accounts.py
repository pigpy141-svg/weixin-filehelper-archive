# -*- coding: utf-8 -*-
"""同步一个或多个微信账号的文件传输助手新增消息。

每个账号独立使用：
- local/keys/<wxid>.json
- local/decrypted/<wxid>/
- local/archive/accounts/<wxid>/

首次发现某个账号时只建立基线，不导出历史旧消息；之后只保存新增。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exporter.lib import accounts as acc


def run_account(account, config, init_only=False, fast=True, backfill_only=False,
                progress_dir=None):
    key, key_file = acc.find_matching_key(account, config)
    if not key:
        return {"wxid": account["wxid"], "ok": False,
                "skipped": "missing_key",
                "error": "缺少该账号的有效密钥，请先在微信解锁后运行密钥提取。"}

    dec_dir = acc.decrypted_dir(account, config)
    out_dir = acc.account_archive(account, config)
    dec_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    argv = [sys.executable, "-u", str(ROOT / "exporter" / "save_new_filehelper.py"),
            str(dec_dir), str(out_dir), str(account["msg"]),
            "--wxid", account["wxid"],
            "--storage", str(account["storage"]),
            "--key-json", str(key_file)]
    if init_only:
        argv.append("--init-baseline")
    if fast:
        argv.append("--fast")
    if backfill_only:
        argv.append("--backfill-only")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if progress_dir:
        env["SYNC_PROGRESS_FILE"] = str(Path(progress_dir) / (account["slug"] + ".json"))
    proc = subprocess.run(argv, cwd=ROOT, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.stdout.decode("utf-8", "replace")
    added = 0
    for line in output.splitlines():
        if "新增" in line and "条文件传输助手消息" in line:
            digits = "".join(ch for ch in line.split("新增", 1)[1] if ch.isdigit())
            if digits:
                added = int(digits)
    return {"wxid": account["wxid"], "slug": account["slug"],
            "dir": str(account["dir"]), "ok": proc.returncode == 0,
            "returncode": proc.returncode, "added": added,
            "key_file": str(key_file), "archive": str(out_dir),
            "output": output[-2000:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "local" / "config.json"))
    ap.add_argument("--wxid", help="只同步指定 wxid")
    ap.add_argument("--all", action="store_true", help="同步所有发现的账号")
    ap.add_argument("--active-only", action="store_true",
                    help="只同步近期有数据库写入的账号（默认）")
    ap.add_argument("--init-baseline", action="store_true")
    ap.add_argument("--no-fast", action="store_true")
    ap.add_argument("--backfill-only", action="store_true")
    ap.add_argument("--json-result", help="把结构化结果写入 JSON 文件")
    args = ap.parse_args()

    config_path = Path(args.config)
    config = acc.load_config(config_path) if config_path.is_file() else {}
    accounts_list = acc.discover_accounts(config)
    if not accounts_list:
        raise SystemExit("找不到微信账号数据目录。")

    if args.wxid:
        accounts_list = [a for a in accounts_list if a["wxid"] == args.wxid]
    elif args.all:
        pass
    else:
        accounts_list = acc.active_accounts(config)

    progress_dir = ROOT / "local" / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for account in accounts_list:
        print(f"[账号] {account['wxid']}  最近写入 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(account['mtime']))}")
        result = run_account(account, config, init_only=args.init_baseline,
                             fast=not args.no_fast, backfill_only=args.backfill_only,
                             progress_dir=progress_dir)
        results.append(result)
        if result.get("skipped") == "missing_key":
            print(f"[跳过] {result['wxid']}：{result['error']}")
        else:
            print(result["output"], end="" if result["output"].endswith("\n") else "\n")

    payload = {"time": int(time.time()), "results": results}
    if args.json_result:
        path = Path(args.json_result)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    failed = [r for r in results if not r["ok"]]
    if failed and all(r.get("skipped") == "missing_key" for r in failed):
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
