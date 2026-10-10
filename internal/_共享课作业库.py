#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树「共享课」作业/单元测试自动化
================================================================

【协议】（详见 共享课作业协议.md）
  主机  studentexam-api.zhihuishu.com/studentExam/gateway/t/v1/
  密钥  onbfhdyvz8x7otrp   IV 1g3qqdh4jvbskb9x
  响应  {"status":"200","rt":{...}}      ← 注意不是 code
  请求  form-urlencoded，body 里 secretStr=<b64>&date=<ms>

【流程】
  ① getStudentHomework    作业列表
  ② doHomework            取题目（含加密 eid）
  ③ getAnswerImgInfo   ★  答案神谕：把选项排到第一位取图，与「A」参考图比对
  ④ saveStudentAnswer     保存答案
  ⑤ submit                交卷

【答案神谕原理】
  服务端按我传的 optionsIdStr 顺序渲染「参考答案 X」图片。
  把某选项放第一位，若图与已知的「A」图字节一致，则该选项正确。
  纯哈希比对，不需要 OCR。

【用法】
  python homework_auto.py --recruit 100001 --course 21000001 --school 369 --list
  python homework_auto.py --recruit 100001 --course 21000001 --school 369 --apply
"""
import os as _os
from pathlib import Path as _P
ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent.parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
CAP.mkdir(parents=True, exist_ok=True)

import argparse
import base64
import hashlib
import json
import re
import time
from pathlib import Path

from Crypto.Cipher import AES
from curl_cffi import requests as cr


SESSION = CAP / "session.json"
REFDIR = CAP / "answer-refs"          # 「参考答案 A/B/C/D」参考图
DB = CAP / "homework_answers.json"    # 题目 → 正确答案（题干哈希作为键）

IV = b"1g3qqdh4jvbskb9x"
KEY = b"onbfhdyvz8x7otrp"
BASE = "https://studentexam-api.zhihuishu.com/studentExam/gateway/t/v1"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def log(*a):
    print(*a, flush=True)


def enc(o):
    raw = json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode()
    n = 16 - len(raw) % 16
    return base64.b64encode(AES.new(KEY, AES.MODE_CBC, IV).encrypt(raw + bytes([n]) * n)).decode()


class Hw:
    def __init__(self):
        s = json.loads(SESSION.read_text(encoding="utf-8"))["studentexam-api.zhihuishu.com"]
        self.s = cr.Session(impersonate="chrome", timeout=30)
        self.s.headers.update({
            "Accept": "*/*", "User-Agent": s.get("ua"),
            "Origin": "https://onlineexamh5new.zhihuishu.com",
            "Referer": "https://onlineexamh5new.zhihuishu.com/",
            "XQJZXHIZ": "8Pf5k3SZClxzWLnff1mMEw==",
            "Cookie": s["cookie"]})
        REFDIR.mkdir(parents=True, exist_ok=True)
        try:
            self.db = json.loads(DB.read_text(encoding="utf-8"))
        except Exception:
            self.db = {}

    def save_db(self):
        DB.write_text(json.dumps(self.db, ensure_ascii=False, indent=1), encoding="utf-8")

    def post(self, path, payload, retries=2):
        t = int(time.time()) * 1000
        p = dict(payload)
        p.setdefault("date", t)
        for _ in range(retries + 1):
            try:
                r = self.s.post(BASE + path,
                                data={"secretStr": enc(p), "date": t}, headers=FORM)
                return json.loads(r.text)
            except Exception as e:
                last = str(e)[:80]
                time.sleep(0.6)
        return {"status": "-1", "rt": None, "msg": last}

    # ---------- 业务 ----------
    def homework_list(self, recruit, course):
        r = self.post("/student/getStudentHomework",
                      {"recruitId": recruit, "courseId": course, "flag": 1,
                       "pageSize": 100, "pageNum": 0})
        return ((r.get("rt") or {}).get("studentHomeworkList")) or []

    def do_homework(self, recruit, exam_id, stu_exam_id, school, course):
        r = self.post("/student/doHomework",
                      {"recruitId": recruit, "examId": exam_id,
                       "studentExamId": stu_exam_id, "schoolId": school,
                       "courseId": str(course)})
        return r.get("rt") or {}

    def answer_img(self, recruit, exam_id, question_id, option_ids):
        """返回 PNG bytes；option_ids 的顺序就是我指定的顺序"""
        sort_info = json.dumps([{"optionsIdStr": ",".join(str(o) for o in option_ids),
                                 "questionId": question_id}], separators=(",", ":"))
        r = self.post("/answer/getAnswerImgInfo",
                      {"optionSortInfo": sort_info, "recruitId": recruit,
                       "examId": exam_id})
        rt = r.get("rt") or {}
        v = rt.get(str(question_id))
        if not isinstance(v, str):
            return None
        m = re.search(r"base64,([A-Za-z0-9+/=]+)", v)
        return base64.b64decode(m.group(1)) if m else None

    def save_answer(self, recruit, exam_id, stu_exam_id, school, eid, answer, qtype=14):
        item = {"examId": exam_id, "recruitId": recruit, "stuExamId": stu_exam_id,
                "eid": eid, "schoolId": school, "deviceId": "", "examType": "",
                "fromType": 3, "answer": answer, "dataIds": "",
                "questionType": qtype}
        r = self.post("/answer/saveStudentAnswer",
                      {"stuExamAnswer": json.dumps([item], ensure_ascii=False,
                                                   separators=(",", ":"))})
        return r

    def submit(self, recruit, exam_id, stu_exam_id, count):
        return self.post("/answer/submit",
                         {"recruitId": recruit, "examId": exam_id,
                          "stuExamId": stu_exam_id, "achieveCount": str(count)})

    # ---------- 答案神谕 ----------
    def _ref(self, letter):
        f = REFDIR / ("ref_%s.png" % letter)
        return f.read_bytes() if f.exists() else None

    def _save_ref(self, letter, data):
        (REFDIR / ("ref_%s.png" % letter)).write_bytes(data)

    def solve(self, recruit, exam_id, question):
        """返回正确 optionId 列表（多选可能有多个）"""
        qid = question["id"]
        opts = [o["id"] for o in (question.get("questionOptions") or [])]
        if not opts:
            return []
        ref_a = self._ref("A")
        # 逐个把候选选项放第一位
        for cand in opts:
            order = [cand] + [o for o in opts if o != cand]
            png = self.answer_img(recruit, exam_id, qid, order)
            if png is None:
                continue
            if ref_a is None:
                self._save_ref("A", png)          # 首次当作 A 的基准
                ref_a = png
            if png == ref_a:
                return [cand]
        # 都不是 A：说明该题有多选或题型特殊，退回第一项
        return [opts[0]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recruit", required=True)
    ap.add_argument("--course", required=True)
    ap.add_argument("--school", required=True)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", help="只做指定 examId")
    a = ap.parse_args()

    h = Hw()
    lst = h.homework_list(a.recruit, a.course)
    log("作业共 %d 份\n" % len(lst))
    log("%-3s %-20s %-12s %-12s %-5s %-5s %-5s %s"
        % ("#", "名称", "stuExamId", "examId", "题数", "分", "重做", "状态"))
    for i, x in enumerate(lst, 1):
        log("%-3d %-20s %-12s %-12s %-5s %-5s %-5s state=%s score=%s"
            % (i, (x.get("examName") or "")[:20], x.get("id"), x.get("examId"),
               x.get("problemNum"), x.get("totalScore"), x.get("backNum"),
               x.get("state"), x.get("score")))

    if a.list or not a.apply:
        log("\n（未加 --apply，仅列出）")
        return

    todo = lst
    if a.only:
        todo = [x for x in lst if x.get("examId") == a.only]

    for i, x in enumerate(todo, 1):
        log("\n" + "=" * 76)
        log("  [%d/%d] %s   examId=%s stuExamId=%s"
            % (i, len(todo), x.get("examName"), x.get("examId"), x.get("id")))
        log("=" * 76)
        rt = h.do_homework(a.recruit, x["examId"], x["id"], a.school, a.course)
        base = rt.get("examBase") or {}
        parts = base.get("workExamParts") or []
        if not parts:
            log("  取题失败: %s" % json.dumps(rt, ensure_ascii=False)[:150])
            continue

        # ---- 收集题目 ----
        qs = []
        for part in parts:
            for q in part.get("questionDtos") or []:
                qs.append(q)
        log("  共 %d 题" % len(qs))

        def answer_all(qlist, use_db=True):
            n = 0
            for q in qlist:
                qid = q["id"]
                name = re.sub(r"<[^>]+>", "", q.get("name") or "")[:38]
                qt = (q.get("questionType") or {}).get("id", 14)
                opts = [o["id"] for o in (q.get("questionOptions") or [])]
                key = hashlib.md5((re.sub(r"<[^>]+>", "", q.get("name") or "")).encode()).hexdigest()
                ans = h.db.get(key) if use_db else None
                if not ans:
                    ans = [opts[0]] if opts else []
                log("     %-38s 类型=%-3s → %s%s"
                    % (name, qt, ans, "  (题库)" if use_db and h.db.get(key) else ""))
                h.save_answer(a.recruit, x["examId"], x["id"], a.school, q.get("eid"),
                              "#@#".join(str(z) for z in ans) if len(ans) > 1 else str(ans[0]),
                              qt)
                n += 1
                time.sleep(0.18)
            return n

        # ---- 第 1 遍 ----
        log("\n  ── 第 1 遍（题库命中优先，其余乱填）──")
        n = answer_all(qs)
        r = h.submit(a.recruit, x["examId"], x["id"], n)
        log("  交卷: %s" % json.dumps(r.get("rt"), ensure_ascii=False)[:120])
        time.sleep(1.5)

        # ---- 收割答案 ----
        log("\n  ── 收割正确答案（getAnswerImgInfo）──")
        got = 0
        for q in qs:
            key = hashlib.md5((re.sub(r"<[^>]+>", "", q.get("name") or "")).encode()).hexdigest()
            ans = h.solve(a.recruit, x["examId"], q)
            if ans:
                h.db[key] = ans
                got += 1
        h.save_db()
        log("  收割 %d/%d 题（题库累计 %d 题）" % (got, len(qs), len(h.db)))
        if got == 0:
            log("  ⚠️ 没收割到，跳过重做")
            continue

        # ---- 第 2 遍 ----
        log("\n  ── 第 2 遍（用正确答案）──")
        rt2 = h.do_homework(a.recruit, x["examId"], x["id"], a.school, a.course)
        qs2 = []
        for part in ((rt2.get("examBase") or {}).get("workExamParts") or []):
            for q in part.get("questionDtos") or []:
                qs2.append(q)
        if not qs2:
            log("  重做取题失败")
            continue
        n2 = answer_all(qs2)
        r2 = h.submit(a.recruit, x["examId"], x["id"], n2)
        log("  ★ 交卷: %s" % json.dumps(r2.get("rt"), ensure_ascii=False)[:120])
        time.sleep(1)


if __name__ == "__main__":
    main()
