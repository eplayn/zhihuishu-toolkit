#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树「AI 智慧课」掌握度（考试）批量刷 —— 分轮次版本
================================================================

【为什么分轮次】
  考试每次从 46 题里随机抽 6 题，交卷后才能看到正确答案。
  如果按「一个知识点考 4 轮」串行做，题库涨得慢、命中率低。
  改成「所有知识点第 1 轮 → 所有第 2 轮 → ...」，
  题库跨节点共享，后面的轮次命中率会明显提高。

【用法】
  python exam_batch.py --course <CID> --class <CLS> --list
  python exam_batch.py --course <CID> --class <CLS> --nodes 5 --rounds 4
  python exam_batch.py --course <CID> --class <CLS> --rounds 6      # 全量

【题库】
  capture/exam_answers.json   {questionId: [optionId, ...]}
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
DB = CAP / "exam_answers.json"

IV = b"1g3qqdh4jvbskb9x"
K_AI = b"hw2fdlwcj4cs1mx7"
K_EXAM = b"onbfhdyvz8x7otrp"
RUN = "https://kg-ai-run.zhihuishu.com/run/gateway/t"
EXAM = "https://studentexamtest.zhihuishu.com/gateway/t/v1"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def log(*a):
    print(*a, flush=True)


def enc(o, key):
    raw = json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode()
    n = 16 - len(raw) % 16
    return base64.b64encode(
        AES.new(key, AES.MODE_CBC, IV).encrypt(raw + bytes([n]) * n)).decode()


class Client:
    def __init__(self):
        s = json.loads(SESSION.read_text(encoding="utf-8"))["studyservice-api.zhihuishu.com"]
        self.s = cr.Session(impersonate="chrome", timeout=30)
        self.s.headers.update({
            "Accept": "*/*", "User-Agent": s.get("ua"),
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://ai-smart-course-student-pro.zhihuishu.com",
            "Referer": "https://ai-smart-course-student-pro.zhihuishu.com/",
            "XQJZXHIZ": "8Pf5k3SZClxzWLnff1mMEw==",
            "Cookie": s["cookie"]})
        try:
            self.db = json.loads(DB.read_text(encoding="utf-8"))
        except Exception:
            self.db = {}

    def save_db(self):
        DB.write_text(json.dumps(self.db, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- kg-ai-run ----
    def ai(self, path, payload):
        t = int(time.time()) * 1000
        p = dict(payload)
        p.setdefault("dateFormate", t)
        try:
            r = self.s.post(RUN + path, json={"secretStr": enc(p, K_AI), "date": t})
            return json.loads(r.text)
        except Exception as e:
            return {"code": -999, "message": str(e)[:70]}

    # ---- studentexamtest ----
    def ex_get(self, path, payload):
        t = int(time.time()) * 1000
        p = dict(payload)
        p["date"] = t
        p["secretStr"] = enc(p, K_EXAM)
        try:
            return json.loads(self.s.get(EXAM + path, params=p).text)
        except Exception as e:
            return {"code": -999, "message": str(e)[:70]}

    def ex_post(self, path, payload):
        t = int(time.time()) * 1000
        p = dict(payload)
        p["date"] = t
        try:
            r = self.s.post(EXAM + path,
                            data={"secretStr": enc(p, K_EXAM), "date": t},
                            headers=FORM)
            return json.loads(r.text)
        except Exception as e:
            return {"code": -999, "message": str(e)[:70]}

    # ---- 一次考试 ----
    def attempt(self, cid, cls, kid):
        t = int(time.time()) * 1000
        r = self.ai("/stu/exam/questions-paper",
                    {"scMapId": cid, "courseId": cid, "classId": cls, "knowledgeId": kid})
        d = r.get("data") or {}
        tid, pid = d.get("examTestId"), d.get("paperId")
        if not tid:
            return None
        self.ex_post("/exam/user/openExam",
                     {"examTestId": tid, "examPaperId": pid, "courseId": cid})

        r = self.ex_get("/exam/user/getExamSheetInfo",
                        {"examTestId": tid, "examPaperId": pid})
        qids = []
        for part in ((r.get("data") or {}).get("partSheetVos") or []):
            for q in part.get("questionSheetVos") or []:
                qids.append(q.get("questionId"))
        if not qids:
            return None

        hit = 0
        for q in qids:
            rr = self.ex_get("/question/getExamQuestionInfo",
                             {"examTestId": tid, "examPaperId": pid, "questionId": q})
            opts = (rr.get("data") or {}).get("optionVos") or []
            cor = self.db.get(str(q))
            if cor:
                cor2 = [c for c in cor if any(str(o["id"]) == str(c) for o in opts)]
                ans = "#@#".join(str(c) for c in cor2) if len(cor2) > 1 else (
                    str(cor2[0]) if cor2 else None)
            else:
                ans = None
            if ans:
                hit += 1
            else:
                ans = str(opts[0]["id"]) if opts else ""
            self.ex_post("/answer/saveAnswer", {
                "recruitId": cid, "examTestId": str(tid), "examPaperId": pid,
                "questionId": q, "dataVos": None, "answer": ans})
            time.sleep(0.1)

        self.ex_post("/exam/user/updateUserUsedTime",
                     {"examTestId": str(tid), "examPaperId": str(pid), "heartbeatTime": 5})
        self.ex_post("/exam/user/submit", {
            "examTestId": str(tid), "courseId": cid, "courseType": 8,
            "examPaperId": str(pid), "aiKnlowledgeId": kid})
        time.sleep(0.8)

        # 收割答案
        got = 0
        for q in qids:
            rr = self.ex_get("/question/getExamQuestionInfo",
                             {"examTestId": tid, "examPaperId": pid, "questionId": q})
            opts = (rr.get("data") or {}).get("optionVos") or []
            cor = [o["id"] for o in opts if o.get("isCorrect") == 1]
            if cor:
                self.db[str(q)] = cor
                got += 1
        if got:
            self.save_db()

        # 对错
        r = self.ex_get("/exam/user/getExamSheetInfo",
                        {"examTestId": tid, "examPaperId": pid})
        right = wrong = 0
        for part in ((r.get("data") or {}).get("partSheetVos") or []):
            for q in part.get("questionSheetVos") or []:
                if q.get("correct") == 1:
                    right += 1
                elif q.get("correct") == 0:
                    wrong += 1

        # 掌握度
        m = self.ai("/stu/mastery-statistics/get-knowledge-mastery-stat",
                    {"courseId": cid, "classId": cls, "knowledgeId": kid})
        md = m.get("data") or {}
        return {"total": len(qids), "hit": hit, "got": got,
                "right": right, "wrong": wrong,
                "mastery": md.get("mastery"), "high": md.get("masteryHigh")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True)
    ap.add_argument("--class", dest="cls", required=True)
    ap.add_argument("--nodes", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    c = Client()

    r = c.ai("/stu/knowledge-study/course-basic", {"courseId": a.course, "classId": a.cls})
    d = r.get("data") or {}
    ks = d.get("knowledgeStatistics") or {}
    log("课程: %s   知识点 %s   进度 %s%%" % (d.get("courseName"), ks.get("knowledgeCount"),
                                             ks.get("studyProgress")))
    nodes = []
    for th in d.get("cakeThemeList", []):
        for k in th.get("knowledgeList") or []:
            nodes.append({"id": k["knowledgeId"], "name": k["knowledgeName"],
                          "sp": k.get("studyProgress"), "mp": k.get("masteryPercentage") or 0})
    if a.only:
        nodes = [n for n in nodes if n["id"] == a.only]
    if a.nodes:
        nodes = nodes[:a.nodes]

    if a.list:
        for i, n in enumerate(nodes, 1):
            log("  %-3d %-34s 进度=%-4s 掌握=%s" % (i, n["name"][:34], n["sp"], n["mp"]))
        return

    log("目标 %d 个知识点，跑 %d 轮    题库现有 %d 题\n" % (len(nodes), a.rounds, len(c.db)))

    best = {n["id"]: n["mp"] for n in nodes}
    for rd in range(1, a.rounds + 1):
        log("=" * 76)
        log("  第 %d / %d 轮      题库 %d 题" % (rd, a.rounds, len(c.db)))
        log("=" * 76)
        todo = 0
        for i, n in enumerate(nodes, 1):
            if best[n["id"]] >= 100:
                continue
            res = c.attempt(a.course, a.cls, n["id"])
            if not res:
                log("  [%2d/%d] %-34s 开卷失败" % (i, len(nodes), n["name"][:34]))
                continue
            best[n["id"]] = max(best[n["id"]], res["mastery"] or 0)
            log("  [%2d/%d] %-34s 命中%2d/%d 对%d错%d  掌握 %s→%s"
                % (i, len(nodes), n["name"][:34], res["hit"], res["total"],
                   res["right"], res["wrong"], res["mastery"], res["high"]))
            todo += 1
            time.sleep(0.6)
        if todo == 0:
            log("\n  全部达标，提前结束")
            break

    log("\n" + "=" * 76)
    log("  题库累计 %d 题" % len(c.db))
    r = c.ai("/stu/knowledge-study/course-basic", {"courseId": a.course, "classId": a.cls})
    for th in ((r.get("data") or {}).get("cakeThemeList") or []):
        for k in th.get("knowledgeList") or []:
            if k["knowledgeId"] in best:
                log("    %-34s 掌握=%s" % (k["knowledgeName"][:34], k.get("masteryPercentage")))


if __name__ == "__main__":
    main()
