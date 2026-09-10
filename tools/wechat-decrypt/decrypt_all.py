# -*- coding: utf-8 -*-
"""
微信 4.x (Windows) 数据库批量解密脚本
用法: python decrypt_all.py <password_hex> <db_dir> <out_dir>
示例: python decrypt_all.py <password_hex> D:\\xwechat_files\\xxx\\db_storage decrypted
依赖: pip install pycryptodome
"""
import os, sys, hashlib, struct, hmac as hmac_mod, time
from Crypto.Cipher import AES

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
SQLITE_HDR = b'SQLite format 3\x00'

def derive_enc_key(password, salt):
    return hashlib.pbkdf2_hmac("sha512", password, salt, 256000, dklen=KEY_SZ)

def derive_mac_key(enc_key, salt):
    mac_salt = bytes(b ^ 0x3a for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)

def verify_page1(enc_key, salt, page1):
    mac_key = derive_mac_key(enc_key, salt)
    hmac_data = page1[SALT_SZ : PAGE_SZ - RESERVE_SZ + 16]
    stored_hmac = page1[PAGE_SZ - HMAC_SZ : PAGE_SZ]
    hh = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hh.update(struct.pack('<I', 1))
    return hh.digest() == stored_hmac

def decrypt_page(enc_key, page_data, pgno):
    iv = page_data[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        encrypted = page_data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        page = SQLITE_HDR + AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted) + b'\x00' * RESERVE_SZ
    else:
        encrypted = page_data[:PAGE_SZ - RESERVE_SZ]
        page = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted) + b'\x00' * RESERVE_SZ
    return page

def decrypt_db(db_path, out_path, enc_key):
    file_size = os.path.getsize(db_path)
    total_pages = (file_size + PAGE_SZ - 1) // PAGE_SZ
    with open(db_path, 'rb') as fin, open(out_path, 'wb') as fout:
        for pgno in range(1, total_pages + 1):
            page_data = fin.read(PAGE_SZ)
            if len(page_data) < PAGE_SZ:
                page_data += b'\x00' * (PAGE_SZ - len(page_data))
            fout.write(decrypt_page(enc_key, page_data, pgno))
    return total_pages

def main():
    if len(sys.argv) < 4:
        print("用法: python decrypt_all.py <password_hex> <db_dir> <out_dir>")
        return
    password = bytes.fromhex(sys.argv[1])
    db_dir, out_dir = sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)

    ok = fail = 0
    t0 = time.time()
    for root, dirs, files in os.walk(db_dir):
        for f in files:
            if not f.endswith('.db') or f.endswith('-wal') or f.endswith('-shm'):
                continue
            path = os.path.join(root, f)
            if os.path.getsize(path) < PAGE_SZ:
                continue
            with open(path, 'rb') as fh:
                page1 = fh.read(PAGE_SZ)
            salt = page1[:SALT_SZ]
            enc_key = derive_enc_key(password, salt)
            if verify_page1(enc_key, salt, page1):
                rel = os.path.relpath(path, db_dir).replace('\\', '_')
                decrypt_db(path, os.path.join(out_dir, rel), enc_key)
                print("[OK] %s" % rel, flush=True)
                ok += 1
            else:
                print("[FAIL] %s (HMAC 验证失败)" % os.path.relpath(path, db_dir), flush=True)
                fail += 1
    print("\n结果: %d OK, %d FAIL, %.0fs" % (ok, fail, time.time() - t0))
    print("输出: %s" % out_dir)

if __name__ == '__main__':
    main()
