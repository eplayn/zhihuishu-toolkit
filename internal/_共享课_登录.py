#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树 · 会话获取 / 续期（二维码登录）

会话里的 jt-cas 是 JWT，有效期 24 小时。过期后所有接口返回 401 Forbidden，
且 CASTGC(TGT) 也一并失效 —— 必须重新走一次登录。

密码登录走不通：实测必须携带易盾滑块 token，否则
  status -1（未带 validate）/ status -4「滑动验证码校验失败」。
所以用二维码登录，只需用「知到」App 扫一次，无需密码、无需验证码。

用法：
  python tools/zhs_login.py            # 生成二维码 -> 扫码 -> 自动保存会话
  python tools/zhs_login.py --check    # 只检查现有会话是否还有效
"""
import os as _os
from pathlib import Path as _P
ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent.parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
CAP.mkdir(parents=True, exist_ok=True)

import argparse
import base64
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from curl_cffi import requests as cr

SESSION_FILE = CAP / "session.json"
QR_PNG = Path(r"F:\Project\pj03\capture\qr_login.png")
API_HOST = "studyservice-api.zhihuishu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

QR_IMG = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg"
QR_INFO = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo"
SERVICE = ("https://cloudapi.polymas.com/oauth/login/ssoLogin"
           "?redirect=https://www.zhihuishu.com")
WAIT_SEC = 300


def log(*a):
    print(*a, flush=True)


def load_session() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def jwt_exp(tok: str):
    try:
        p = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("exp")
    except Exception:
        return None


def new_session() -> cr.Session:
    s = cr.Session(impersonate="chrome", timeout=25, allow_redirects=True)
    s.headers.update({"Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", "User-Agent": UA})
    return s


def collect_cookie_header(s: cr.Session) -> str:
    pairs, seen = [], set()
    for c in s.cookies.jar:
        dom = (c.domain or "").lstrip(".")
        if dom not in ("zhihuishu.com", "polymas.com"):
            continue
        if c.name in seen:
            continue
        seen.add(c.name)
        pairs.append(c.name + "=" + c.value)
    return "; ".join(pairs)


def save_session(cookie_header: str, extra: dict = None):
    data = load_session()
    rec = {"cookie": cookie_header, "ua": UA}
    if extra:
        rec.update(extra)
    data[API_HOST] = rec
    SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def verify(sess_cookie: str):
    """用给定 cookie 调 queryCourse 验证会话是否有效"""
    from Crypto.Cipher import AES
    IV = b"1g3qqdh4jvbskb9x"
    KEY = b"azp53h0kft7qi78q"

    def enc(o):
        raw = json.dumps(o, separators=(",", ":")).encode()
        n = 16 - len(raw) % 16
        raw += bytes([n]) * n
        return base64.b64encode(AES.new(KEY, AES.MODE_CBC, IV).encrypt(raw)).decode()

    s = cr.Session(impersonate="chrome", timeout=25)
    s.headers.update({
        "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://studyvideoh5.zhihuishu.com",
        "Referer": "https://studyvideoh5.zhihuishu.com/",
        "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
        "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
        "Cookie": sess_cookie,
    })
    t = int(time.time()) * 1000
    body = {"secretStr": enc({"recruitAndCourseId": "<recruit-secret>",
                              "dateFormate": t}), "dateFormate": str(t)}
    try:
        r = s.post("https://%s/gateway/t/v1/learning/queryCourse" % API_HOST, data=body)
        j = json.loads(r.text)
        return j.get("code") == 0, "code=%s %s" % (j.get("code"), j.get("message"))
    except Exception as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--qr-only", action="store_true")
    a = ap.parse_args()

    if a.check:
        ck = load_session().get(API_HOST, {}).get("cookie", "")
        if not ck:
            log("session.json 里没有会话，请先跑一次登录")
            return
        m = re.search(r"jt-cas=([^;]+)", ck)
        if m:
            e = jwt_exp(m.group(1))
            if e:
                now = time.time()
                st = ("剩余 %.1f 小时" % ((e - now) / 3600)) if now < e \
                    else ("已过期 %.1f 小时" % ((now - e) / 3600))
                log("jt-cas 到期: %s   %s" % (datetime.fromtimestamp(e).strftime("%Y-%m-%d %H:%M:%S"), st))
        ok, msg = verify(ck)
        log("接口实测: %s  (%s)" % ("会话有效" if ok else "会话失效", msg))
        return

    s = new_session()

    log("请求二维码 ...")
    j = s.get(QR_IMG).json()
    qr_token = j.get("qrToken")
    img_b64 = j.get("img")
    if not qr_token or not img_b64:
        log("二维码获取失败: " + str(j)[:300])
        return
    QR_PNG.write_bytes(base64.b64decode(img_b64))
    log("二维码已保存: " + str(QR_PNG))
    log("qrToken: " + qr_token)
    try:
        os.startfile(str(QR_PNG))
        log("已自动打开图片 —— 请用「知到」App 扫码")
    except Exception:
        log("请手动打开 " + str(QR_PNG) + " 扫码")

    if a.qr_only:
        return

    log("\n等待扫码（最长 %d 秒，每 0.5 秒轮询）..." % WAIT_SEC)
    scanned = False
    deadline = time.time() + WAIT_SEC
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            msg = s.get(QR_INFO, params={"qrToken": qr_token}).json()
        except Exception:
            continue
        st = msg.get("status")
        if st == -1:
            continue
        if st == 0:
            if not scanned:
                scanned = True
                log("已扫码，请在手机上确认")
            continue
        if st == 1:
            log("确认成功，正在换取登录态 ...")
            once = msg.get("oncePassword")
            rr = s.get("https://passport.zhihuishu.com/login",
                       params={"pwd": once, "service": SERVICE})
            log("   最终 URL: " + rr.url[:110])
            log("   跳转 %d 跳: %s" % (len(rr.history), " -> ".join(str(h.status_code) for h in rr.history)))
            ck = collect_cookie_header(s)
            names = [p.split("=")[0].strip() for p in ck.split(";") if "=" in p]
            log("   收到 cookie: " + str(names))
            if "CASLOGC" not in names:
                log("   没拿到 CASLOGC，登录可能未完成")
                return
            try:
                from urllib.parse import unquote
                uuid = json.loads(unquote(re.search(r"CASLOGC=([^;]+)", ck).group(1)))["uuid"]
                if ("exitRecod_" + uuid) not in ck:
                    ck += "; exitRecod_%s=2" % uuid
                log("   uuid = " + uuid)
            except Exception as e:
                log("   解析 uuid 失败: %s" % e)

            ok, vmsg = verify(ck)
            save_session(ck, {"loginAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            if ok:
                log("\n会话已保存到 " + str(SESSION_FILE))
                log("   验证通过: " + vmsg)
            else:
                log("\n拿到 cookie 但接口验证失败: " + vmsg)
                log("   仍已写入 session.json 供排查")
            return
        if st == 2:
            log("二维码过期，请重新运行本脚本")
            return
        if st == 3:
            log("用户取消")
            return
    log("超时（%d 秒），请重新运行" % WAIT_SEC)


if __name__ == "__main__":
    main()
