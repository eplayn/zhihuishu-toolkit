#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树 · 课程总览（一次列出名下所有课程 + 进度）
================================================================
数据来源（都是 onlineservice-api）：
  共享课   POST /gateway/t/v1/student/course/share/queryShareCourseInfo
           → progress "100.0%", recruitId, secret
  新形态课 POST /gateway/t/v1/student/queryStudentAICourseList
           → schedule（学习进度）, mastery（掌握度）, classId

加密：AES-128-CBC / IV=1g3qqdh4jvbskb9x / key=7q9oko0vqb3la20r（HOME）
请求体：{"secretStr":"<b64>","date":<ms>}

用法：
  python _课程总览.py            # 列出全部
  python _课程总览.py --json     # 输出 JSON（供脚本调用）
"""
import argparse
import base64
import json
import os as _os
import sys
import time
from pathlib import Path as _P

ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent.parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
SESSION = CAP / "session.json"

from Crypto.Cipher import AES
from curl_cffi import requests as cr

IV = b"1g3qqdh4jvbskb9x"
KEY = b"7q9oko0vqb3la20r"          # HOME_KEY（module 6/默认）
BASE = "https://onlineservice-api.zhihuishu.com/gateway/t/v1"


def log(*a):
    print(*a, flush=True)


def enc(o):
    raw = json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode()
    n = 16 - len(raw) % 16
    return base64.b64encode(
        AES.new(KEY, AES.MODE_CBC, IV).encrypt(raw + bytes([n]) * n)).decode()


class CourseList:
    def __init__(self):
        s = json.loads(SESSION.read_text(encoding="utf-8"))
        # 优先用 onlineservice 自己的 cookie，没有就退回 studyservice
        node = s.get("onlineservice-api.zhihuishu.com") or s.get("studyservice-api.zhihuishu.com")
        if not node:
            raise SystemExit("❌ session.json 里没有可用会话，先跑 _共享课_登录.py")
        self.s = cr.Session(impersonate="chrome", timeout=30)
        self.s.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": node.get("ua") or "",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://onlineweb.zhihuishu.com",
            "Referer": "https://onlineweb.zhihuishu.com/",
            "Cookie": node["cookie"]})

    def post(self, path, payload):
        t = int(time.time()) * 1000
        p = dict(payload)
        p.setdefault("date", t)
        try:
            # ★ onlineservice 必须 form-urlencoded，用 json 会报 aes加密参数异常
            r = self.s.post(BASE + path,
                            data={"secretStr": enc(p), "date": t},
                            headers={"Content-Type":
                                     "application/x-www-form-urlencoded"})
            return json.loads(r.text)
        except Exception as e:
            return {"code": -999, "message": str(e)[:90]}

    # ---------- 共享课 ----------
    def share(self, page=1, size=50):
        r = self.post("/student/course/share/queryShareCourseInfo",
                      {"status": 0, "pageNo": page, "pageSize": size})
        return ((r.get("result") or {}).get("courseOpenDtos")) or []

    # ---------- 新形态课程 ----------
    def ai(self, status=3):
        r = self.post("/student/queryStudentAICourseList", {"status": status})
        return r.get("rt") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    c = CourseList()
    out = {"share": [], "ai": []}

    log("=" * 96)
    log("  共享课")
    log("=" * 96)
    try:
        sl = c.share()
    except Exception as e:
        sl = []
        log("  ❌ 获取失败: %s" % e)
    if not sl:
        log("  （无 / 获取失败）")
    for x in sl:
        rec = {"name": x.get("courseName"), "progress": x.get("progress"),
               "recruitId": x.get("recruitId"), "courseId": x.get("courseId"),
               "teacher": x.get("teacherName"), "school": x.get("schoolName"),
               "lesson": x.get("lessonName"), "secret": x.get("secret"),
               "courseType": x.get("courseType")}
        out["share"].append(rec)
        log("  %-24s 进度 %-8s  recruitId=%-8s courseId=%-12s %s"
            % (rec["name"], rec["progress"], rec["recruitId"], rec["courseId"], rec["teacher"]))

    log("")
    log("=" * 96)
    log("  新形态课程（AI 智慧课）")
    log("=" * 96)
    try:
        al = c.ai()
    except Exception as e:
        al = []
        log("  ❌ 获取失败: %s" % e)
    if not al:
        log("  （无 / 获取失败）")
    for x in al:
        rec = {"name": x.get("courseName"), "schedule": x.get("schedule"),
               "mastery": x.get("mastery"), "courseId": x.get("courseId"),
               "classId": x.get("classId"), "className": x.get("className"),
               "teacher": x.get("teacherUserName"), "schoolId": x.get("schoolId"),
               "status": x.get("status"), "linkUrl": x.get("linkUrl")}
        out["ai"].append(rec)
        log("  %-28s 学习 %-5s 掌握 %-5s  courseId=%-20s classId=%-8s %s"
            % (rec["name"], rec["schedule"], rec["mastery"],
               rec["courseId"], rec["classId"], rec["teacher"]))

    outfile = CAP / "my_courses.json"
    outfile.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log("")
    log("=" * 96)
    log("  共享课 %d 门   新形态课 %d 门   已存 %s"
        % (len(out["share"]), len(out["ai"]), outfile))

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
