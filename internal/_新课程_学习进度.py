#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树「AI 智慧课 / 新形态课程」自动刷课
================================================================

【协议要点】（详见 新形态课程协议分析.md）
  * 请求体 AES-128-CBC / Pkcs7 / IV=1g3qqdh4jvbskb9x / key=hw2fdlwcj4cs1mx7
  * body = {"secretStr": <b64>, "date": <epoch_ms>}
  * 响应是明文 JSON，code=200 为成功
  * 必需请求头 XQJZXHIZ（会话内恒定）

【完成机制】（实测确定）
  * PPT   (resourcesDataType=11) -> 调 completed 即可
  * 视频  (resourcesDataType=21) -> 调 completed 即可
  * 视频  (resourcesDataType=22) -> 需 lastWatchTime >= refPartTime，再 completed

【用法】
  python newcourse_auto.py --course <COURSE_ID> --class <CLASS_ID> [选项]

  --list        只列出知识点与进度
  --dry         预演（不提交）
  --nodes N     最多刷 N 个知识点（默认全部）
  --only <kid>  只刷指定知识点
  --speed N     report 之间的间隔秒数（默认 0.35）
"""
import os as _os
from pathlib import Path as _P
ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent.parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
CAP.mkdir(parents=True, exist_ok=True)

import argparse
import base64
import json
import time
from pathlib import Path

from Crypto.Cipher import AES
from curl_cffi import requests as cr


SESSION = CAP / "session.json"

AES_KEY = b"hw2fdlwcj4cs1mx7"
AES_IV = b"1g3qqdh4jvbskb9x"
XQ = "8Pf5k3SZClxzWLnff1mMEw=="

RUN = "https://kg-ai-run.zhihuishu.com/run/gateway/t"

DT_PPT = 11
DT_VIDEO_SYNC = 21
DT_VIDEO_OSS = 22


def log(*a):
    print(*a, flush=True)


def enc(obj):
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
    n = 16 - len(raw) % 16
    return base64.b64encode(
        AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(raw + bytes([n]) * n)).decode()


class Client:
    def __init__(self):
        s = json.loads(SESSION.read_text(encoding="utf-8"))["studyservice-api.zhihuishu.com"]
        self.s = cr.Session(impersonate="chrome", timeout=25)
        self.s.headers.update({
            "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": s.get("ua"),
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://ai-smart-course-student-pro.zhihuishu.com",
            "Referer": "https://ai-smart-course-student-pro.zhihuishu.com/",
            "XQJZXHIZ": XQ,
            "Cookie": s["cookie"],
        })

    def post(self, path, payload):
        t = int(time.time() * 1000)
        p = dict(payload)
        p.setdefault("dateFormate", t)
        try:
            r = self.s.post(RUN + path, json={"secretStr": enc(p), "date": t})
            return json.loads(r.text)
        except Exception as e:
            return {"code": -999, "message": str(e)[:120]}


class Course:
    def __init__(self, c, course_id, class_id):
        self.c = c
        self.course_id = course_id
        self.class_id = class_id
        self.nodes = []
        self.name = ""
        self.stat = {}

    def load(self):
        r = self.c.post("/stu/knowledge-study/course-basic",
                        {"courseId": self.course_id, "classId": self.class_id})
        if r.get("code") != 200:
            log("读取课程失败: %s" % json.dumps(r, ensure_ascii=False)[:200])
            return False
        d = r["data"]
        self.name = d.get("courseName")
        self.stat = d.get("knowledgeStatistics", {})
        self.nodes = []
        for th in d.get("cakeThemeList", []):
            for k in th.get("knowledgeList") or []:
                self.nodes.append({
                    "theme": th["themeName"], "id": k["knowledgeId"],
                    "name": k["knowledgeName"], "progress": k.get("studyProgress") or 0})
        return True

    def resources(self, kid):
        r = self.c.post("/stu/resources/list-knowledge-resource",
                        {"courseId": self.course_id, "classId": self.class_id, "knowledgeId": kid})
        return (r.get("data") or {}).get("resourceList") or []

    def mark(self, kid, uid):
        return self.c.post("/stu/studyRecord/completed", {
            "courseId": self.course_id, "knowledgeId": kid,
            "classId": self.class_id, "resourcesUid": uid, "watchUId": 1})

    def beat(self, kid, resource_id=None, file_id=None, last_watch=None):
        p = {"courseId": self.course_id, "knowledgeId": kid}
        if resource_id:
            p["resourceId"] = resource_id
        if file_id is not None:
            p["fileId"] = file_id
            p["lastWatchTime"] = last_watch
        return self.c.post("/stu/studyRecord/study", p)

    def report(self, kid, file_id, last_watch, delta=10):
        return self.c.post("/stu/studyRecord/report", {
            "courseId": self.course_id, "classId": self.class_id,
            "fileId": file_id, "studyTotalTime": delta, "lastWatchTime": last_watch,
            "knowledgeId": kid, "shareCourseId": "", "watchUId": 1, "nodeType": 0})

    def load_resource(self, kid, rd):
        uid = rd["resourcesUid"]
        dt = rd.get("resourcesDataType")
        if dt == DT_PPT:
            self.c.post("/stu/resources-lab/get-ppt-page", {
                "courseId": self.course_id, "classId": self.class_id,
                "resourcesUidList": [uid], "nodeUid": kid})
            self.c.post("/stu/resources-lab/get-ppt-detail-v2", {
                "resourcesUid": uid, "nodeUid": kid, "onlyResourceUid": False,
                "classId": self.class_id, "courseId": self.course_id})
        else:
            vid = rd.get("resourcesFileSubId") if dt == DT_VIDEO_SYNC else rd.get("resourcesFileId")
            if vid:
                self.c.post("/stu/resources-lab/get-video-time", {
                    "courseId": self.course_id, "classId": self.class_id,
                    "videoIdList": [str(vid)]})

    def node_time(self, kid):
        """本节点每个资源的已学时长 {resourcesUid: seconds}"""
        r = self.c.post("/stu/studyRecord/get-knowledge-video-time",
                        {"courseId": self.course_id, "knowledgeId": kid, "classId": self.class_id})
        return r.get("data") or {}

    def video_time(self, vid):
        r = self.c.post("/stu/resources-lab/get-video-time", {
            "courseId": self.course_id, "classId": self.class_id,
            "videoIdList": [str(vid)]})
        d = r.get("data")
        if isinstance(d, list) and d:
            return d[0].get("time") or 0
        return 0

    def finish(self, node, speed=0.35, dry=False):
        kid = node["id"]
        log("\n▶ %s   [%s]   当前 %s%%" % (node["name"], kid, node["progress"]))
        self._ntime = self.node_time(kid)
        res = self.resources(kid)
        if not res:
            log("   （无资源）")
            return 0
        n = 0
        for it in res:
            rd = it.get("resourcesDetail") or {}
            uid, dt = rd.get("resourcesUid"), rd.get("resourcesDataType")
            log("   ── %-34s dt=%s" % ((rd.get("resourcesName") or "")[:34], dt))
            if dry:
                continue

            self.load_resource(kid, rd)

            if dt == DT_VIDEO_OSS:
                fid = rd.get("resourcesFileId")
                try:
                    need = int(rd.get("refPartTime") or 0)
                except Exception:
                    need = 0
                cur = int(self._ntime.get(uid) or 0)
                if cur < need:
                    pos = cur + 10
                    while pos <= need + 20:
                        self.beat(kid, file_id=fid, last_watch=pos)
                        rr = self.report(kid, fid, pos)
                        if rr.get("code") != 200:
                            log("      report pos=%d -> code=%s %s"
                                % (pos, rr.get("code"), str(rr.get("message"))[:40]))
                            break
                        pos += 10
                        time.sleep(speed)
                    log("      观看时长 %d -> %s 秒（门槛 %d）" % (cur, pos - 10, need))
                else:
                    log("      已有 %d 秒（门槛 %d），跳过" % (cur, need))

            rc = self.mark(kid, uid)
            log("      completed code=%s" % rc.get("code"))
            self.beat(kid, resource_id=uid)
            self.mark(kid, uid)
            n += 1
            time.sleep(0.3)
        return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True, help="courseId (scMapUid)")
    ap.add_argument("--class", dest="cls", required=True, help="classId")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--nodes", type=int, default=0)
    ap.add_argument("--only")
    ap.add_argument("--speed", type=float, default=0.35)
    a = ap.parse_args()

    c = Client()
    co = Course(c, a.course, a.cls)
    log("=" * 74)
    if not co.load():
        return
    log("  课程: %s" % co.name)
    log("  知识点 %d   当前进度 %d%%"
        % (co.stat.get("knowledgeCount"), co.stat.get("studyProgress")))
    log("=" * 74)

    if a.list:
        for i, n in enumerate(co.nodes, 1):
            m = "OK " if n["progress"] >= 100 else ("~~ " if n["progress"] > 0 else "   ")
            log("  %-3d %s %-38s %3s%%" % (i, m, n["name"][:38], n["progress"]))
        return

    todo = [n for n in co.nodes if n["progress"] < 100]
    if a.only:
        todo = [n for n in co.nodes if n["id"] == a.only]
    if a.nodes:
        todo = todo[:a.nodes]

    log("\n待处理 %d 个知识点%s" % (len(todo), "（预演）" if a.dry else ""))
    for i, n in enumerate(todo, 1):
        log("\n" + "-" * 74 + "\n  [%d/%d]" % (i, len(todo)))
        co.finish(n, speed=a.speed, dry=a.dry)

    if not a.dry:
        co.load()
        log("\n" + "=" * 74)
        log("  课程总进度: %d%%" % co.stat.get("studyProgress"))


if __name__ == "__main__":
    main()
