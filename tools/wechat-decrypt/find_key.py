# -*- coding: utf-8 -*-
"""
微信 4.1.12.x (Windows x64) 数据库主密钥获取脚本
原理: Frida spawn 微信, hook codec 配置函数 (weixin.dll 偏移 0x3486140),
      rcx 指向的结构前 32 字节 = password (原始密钥)
用法: python find_key.py [Weixin.exe路径]
输出: 密钥 hex 写入 key.txt
依赖: pip install frida
"""
import frida, time, sys

WEIXIN_PATH = sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files\Tencent\Weixin\Weixin.exe"
OUT_FILE = "key.txt"
CODEC_CFG_OFF = 0x3486140  # weixin.dll 偏移 (4.1.12.26 验证)

js = r"""
var LOG = "hook.log";
function log(s) {
    var f = new File(LOG, "a");
    f.write(s + "\n");
    f.close();
}
function setup() {
    var mod = Process.getModuleByName("Weixin.dll");
    if (!mod) return false;
    var base = mod.base;
    Interceptor.attach(base.add(0x3486140), {
        onEnter: function(args) {
            var c = this.context;
            log("=== CODEC_CFG_FUNC triggered ===");
            log("rcx=" + c.rcx + " rdx=" + c.rdx);
            try {
                var buf = Memory.readByteArray(c.rcx, 64);
                var u8 = new Uint8Array(buf);
                var hex = "";
                for (var i = 0; i < u8.length; i++) hex += ("0" + u8[i].toString(16)).slice(-2);
                log("rcx[64]=" + hex);
                log("PASSWORD_CANDIDATE[32]=" + hex.slice(0, 64));
            } catch(e) { log("read fail: " + e); }
        }
    });
    log("CODEC_CFG hooked at " + base.add(0x3486140));
    return true;
}
var tries = 0;
function trySetup() {
    try { if (setup()) return; } catch(e) { log("err: " + e); }
    tries++;
    if (tries > 60) { log("GIVEUP: Weixin.dll 未加载"); return; }
    setTimeout(trySetup, 2000);
}
trySetup();
"""

log("spawning: %s" % WEIXIN_PATH)
device = frida.get_local_device()
pid = device.spawn([WEIXIN_PATH])
log("spawned pid=%d, 等待扫码登录..." % pid)
session = device.attach(pid)
script = session.create_script(js)
script.on("message", lambda msg, data: None)
script.load()
device.resume(pid)
log("resumed - 微信已启动, 请扫码登录 (密钥在启动早期自动捕获)")
# 保持运行等待捕获
time.sleep(120)
session.detach()
log("DONE - 检查 hook.log 中的 PASSWORD_CANDIDATE")
