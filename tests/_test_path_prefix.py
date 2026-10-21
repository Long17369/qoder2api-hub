"""路径前缀重写套件：配置层 + 接线层。

    python tests/_test_path_prefix.py

配置层：normalize_path_prefix 清洗 / path_prefix 读 / set_path_prefix 写。
接线层：真 Handler 起本地服务，验证 /前缀/v1/models、/前缀/ping、/前缀/ 看板
都能按剥掉前缀后的路径路由；不带前缀的原路径行为不变；CORS 判定在前缀剥除
之后进行。「不发上游」：GET 全部走本地桩/静态路径，无需外网。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ACCOUNTS_DIR", os.path.join(ROOT, "_acc"))
import qoder_settings as S

PASS = FAIL = 0


def check(label, ok, extra=None):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] %s  %r" % (label, extra))


D = tempfile.mkdtemp(prefix="qd-prefix-")

# ---- 指纹行（与前端 _test_dashboard_*.js 同格式；实现见 tests/_suite_head.py）----
import os as _os_fp
import sys as _sys_fp
_sys_fp.path.insert(0, _os_fp.path.dirname(_os_fp.path.abspath(__file__)))
from _suite_head import declare_suite
declare_suite(__file__, ["qoder_proxy.py", "qoder_settings.py"])
print("[path-prefix] 配置层")

_norm = S.normalize_path_prefix

check("N1 基本清洗：qoder -> /qoder；/qoder/ -> /qoder（去尾斜杠）",
      _norm("qoder") == "/qoder" and _norm("/qoder/") == "/qoder", None)
check("N2 空 / 仅斜杠 / None = 关闭（空串）",
      _norm("") == "" and _norm("   ") == "" and _norm("/") == ""
      and _norm(None) == "", None)
check("N3 非法字符 / 超长 / 路径穿越 -> 回落关闭（读侧从不抛）",
      _norm("/a b") == "" and _norm("/中") == "" and _norm("/a?q") == ""
      and _norm("/" + "a" * 130) == "" and _norm("/../etc") == "", None)
check("N4 多段前缀保留：/api/qoder 原样可用",
      _norm("api/qoder") == "/api/qoder", None)

check("C1 默认关闭：未配置 -> path_prefix == ''",
      S.path_prefix(D) == "", S.path_prefix(D))
check("C2 set/read 往返；空值 = 清除",
      S.set_path_prefix(D, "qoder") == "/qoder"
      and S.path_prefix(D) == "/qoder"
      and S.set_path_prefix(D, "") == "" and S.path_prefix(D) == "", None)
_bad = False
try:
    S.set_path_prefix(D, "/不 合法")
except ValueError:
    _bad = True
check("C3 写侧非法值抛 ValueError（读侧不受影响）", _bad, None)

shutil.rmtree(D, ignore_errors=True)

# ================= 接线层：真 handler，本地起服 =================
print()
print("[path-prefix] 接线层（真 handler）")

import http.client
import http.server
import threading

import qoder_proxy as P

_G = tempfile.mkdtemp(prefix="qd-prefix-live-")
_orig_accounts = P.ACCOUNTS_DIR
P.ACCOUNTS_DIR = _G

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
_PORT = srv.server_address[1]


def _get(path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=20)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return int(resp.status), resp.read().decode("utf-8", "replace"), resp
    finally:
        conn.close()


def _options(path):
    conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=20)
    try:
        conn.request("OPTIONS", path)
        resp = conn.getresponse()
        return int(resp.status), resp
    finally:
        conn.close()


try:
    # 前缀未配置：一切照旧
    code, text, _ = _get("/ping")
    check("W0 基线：未配置前缀时 /ping 正常（行为不变）",
          code == 200 and "pong" in text, (code, text[:40]))

    P.ACCOUNTS_DIR = _G
    S.set_path_prefix(_G, "qoder")

    code, text, _ = _get("/qoder/ping")
    check("W1 带前缀 /qoder/ping -> 200 pong（重写生效）",
          code == 200 and "pong" in text, (code, text[:40]))

    code, text, _ = _get("/ping")
    check("W2 不带前缀的原路径仍可达（挂载是可选的，不是强制的）",
          code == 200 and "pong" in text, (code, text[:40]))

    code, text, _ = _get("/qoder/v1/models")
    check("W3 带前缀 /qoder/v1/models 进 Key 鉴权分支（路由到 API 层）",
          code in (200, 401), (code, text[:80]))

    code, text, _ = _get("/qoder/")
    check("W4 带前缀看板 /qoder/ -> 200 HTML",
          code == 200 and "<html" in text.lower(), (code, text[:60]))

    code, text, _ = _get("/qoder")
    check("W4b 精确前缀 /qoder -> 重写为 / -> 200 HTML",
          code == 200 and "<html" in text.lower(), (code, text[:60]))

    code, resp = _options("/qoder/v1/chat/completions")
    check("W5 OPTIONS 带前缀：前缀先剥再判 CORS -> 204 + ACAO 头",
          code == 204 and bool(resp.getheader("Access-Control-Allow-Origin")),
          (code, resp.getheader("Access-Control-Allow-Origin")))

    code, resp = _options("/qoder/usage")
    check("W5b OPTIONS 管理路径带前缀：仍无 ACAO（剥前缀后的判定不变）",
          code == 204 and not resp.getheader("Access-Control-Allow-Origin"),
          (code, resp.getheader("Access-Control-Allow-Origin")))

    # 管理端点在前缀下同样要面板会话（重写不改变鉴权）
    code, text, _ = _get("/qoder/settings")
    check("W6 /qoder/settings 未带面板会话 -> 401（鉴权语义不变）",
          code == 401, (code, text[:80]))

    # 保存端点回环验证：settings 视图带 path_prefix
    S.set_path_prefix(_G, "/api/qoder")
    code, text, _ = _get("/api/qoder/ping")
    check("W7 多段前缀 /api/qoder 生效",
          code == 200 and "pong" in text, (code, text[:40]))

    view_keys = ("path_prefix",)
    view = P.runtime_settings_view()
    check("W8 runtime_settings_view 暴露 path_prefix（面板回显用）",
          all(k in view for k in view_keys) and view["path_prefix"] == "/api/qoder",
          view.get("path_prefix"))
finally:
    try:
        srv.shutdown()
    except Exception:
        pass
    P.ACCOUNTS_DIR = _orig_accounts
    shutil.rmtree(_G, ignore_errors=True)

print("")
print("SUMMARY: TOTAL %d checks, %d passed, %d failed" % (PASS + FAIL, PASS, FAIL))
print("RESULT: %s (exit %d)" % ("RED" if FAIL else "GREEN", 1 if FAIL else 0))
sys.exit(1 if FAIL else 0)
