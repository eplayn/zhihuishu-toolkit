#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享课 · 自动刷作业（带可靠答案神谕）
================================================================
被 zhs.py 调用。

【答案怎么来的】
  交卷后 lookHomework 会给出**数字 questionId**（交卷前只有不可逆的 eid）。
  拿数字 id 调 getAnswerImgInfo，返回一张图，内容是「参考答案 X」。
  X = 我传进去的选项顺序里的位置。

  实测：这 4 张图**只跟字母有关，与题目无关**，所以只要有一张 A/B/C/D 的
  参考图（sha→字母），把选项按原序传进去，查图就能直接定位正确选项。

  参考图靠**自标定**得到，不写死：
    交卷后 checkAnswerSheetInfo 会告诉我哪几题对了，
    对的那题我知道正确选项 → 把它放到第 1/2/3/4 位各取一次图 → 标定出 A/B/C/D。

【流程】对每份作业：
  ① 需要就申请重做
  ② 取题 → 答题（题库优先，否则第一个）→ 校验落库 → 交卷
  ③ 诊断：lookHomework + checkAnswerSheetInfo + getAnswerImgInfo
  ④ 自标定字母表（用答对的题）
  ⑤ 收割所有题的正确选项 → 存题库
  ⑥ 还没满分且还有重做次数 → 重做 → 用答案交 → 满分
  ⑦ 报进度；全部跑完出总表

遇到滑块直接报错，不做处理。
"""
import argparse
import base64
import hashlib
import json
import os as _os
import re
import sys
import time
from pathlib import Path as _P

ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent.parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
sys.path.insert(0, str(_P(__file__).resolve().parent))

from _共享课作业库 import Hw

DB = CAP / "homework_answers.json"
KEEP_REDO = 1        # ★ 永远给用户保留 N 次重做机会，脚本不许动用
LETTERMAP_FILE = CAP / "answer_letter_map.json"

# 实测标定出来的兜底表（服务端改渲染时会自动重新标定）
DEFAULT_LETTER_SHA = {
    "ca666992313c38c417e2e8d6a218a0a39a2dd57c49c14d45f50a27b7f502958d": 0,  # A
    "86d6f896b733a2350816cdefaee9fc7507f88b90cfe99728dc6d3ee7a80d9e0c": 1,  # B
    "ebbe56947874ba1aadf237de34e5f90bd167412fc60fbe4bd957ac9ae16bc805": 2,  # C
    "bbce2edba3e0e726bcf85416424defb4a59c37b6d9c46087930fdb62255771d4": 3,  # D
}


def log(*a):
    print(*a, flush=True)


def plain(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def qkey(q):
    return hashlib.md5(plain(q.get("name")).encode()).hexdigest()


class Slider(Exception):
    pass


class Runner:
    def __init__(self, R, C, S, rounds=3):
        self.h = Hw()
        self.R, self.C, self.S = R, C, S
        self.max_rounds = rounds
        try:
            self.db = json.loads(DB.read_text(encoding="utf-8"))
        except Exception:
            self.db = {}
        try:
            self.lmap = json.loads(LETTERMAP_FILE.read_text(encoding="utf-8"))
            self.lmap = {k: int(v) for k, v in self.lmap.items()}
        except Exception:
            self.lmap = dict(DEFAULT_LETTER_SHA)

    @property
    def rev(self):
        """字母下标 → sha（同一下标可能有多张图，取默认表里的）"""
        out = {}
        for sha, idx in DEFAULT_LETTER_SHA.items():
            out.setdefault(idx, sha)
        for sha, idx in self.lmap.items():
            out.setdefault(idx, sha)
        return out

    def save_db(self):
        DB.write_text(json.dumps(self.db, ensure_ascii=False, indent=1), encoding="utf-8")

    def save_lmap(self):
        LETTERMAP_FILE.write_text(json.dumps(self.lmap, ensure_ascii=False, indent=1),
                                  encoding="utf-8")

    # ---------- 基础 ----------
    def list_hw(self):
        out = []
        for flag in (1, 2):
            r = self.h.post("/student/getStudentHomework",
                            {"recruitId": self.R, "courseId": self.C, "flag": flag,
                             "pageSize": 100, "pageNum": 0})
            for x in ((r.get("rt") or {}).get("studentHomeworkList") or []):
                x["_flag"] = flag
                out.append(x)
        return out

    def redo_left(self, exam_id):
        rn = self.h.post("/student/redoNum", {"recruitId": self.R, "examId": exam_id}).get("rt")
        try:
            return 3 - int(rn or 0)
        except Exception:
            return 0

    def apply_redo(self, exam_id):
        r = self.h.post("/student/saveCourseTran",
                        {"recruitId": self.R, "examId": exam_id,
                         "description": "", "courseId": self.C})
        return r.get("rt") == 1

    def questions(self, exam_id, stu_id):
        rt = self.h.do_homework(self.R, exam_id, stu_id, self.S, self.C)
        return [q for p in ((rt.get("examBase") or {}).get("workExamParts") or [])
                for q in (p.get("questionDtos") or [])]

    def answer_img(self, exam_id, qid, order):
        """按指定顺序取「参考答案 X」图，返回 bytes"""
        si = json.dumps([{"optionsIdStr": ",".join(str(o) for o in order),
                          "questionId": qid}], separators=(",", ":"))
        r = self.h.post("/answer/getAnswerImgInfo",
                        {"optionSortInfo": si, "recruitId": self.R, "examId": exam_id})
        v = (r.get("rt") or {}).get(str(qid))
        if not (isinstance(v, str) and "base64" in v):
            return None
        m = re.search(r"base64,([A-Za-z0-9+/=]+)", v)
        return base64.b64decode(m.group(1)) if m else None

    def save_one(self, exam_id, stu_id, q, answer):
        r = self.h.save_answer(self.R, exam_id, stu_id, self.S, q.get("eid"),
                               answer, (q.get("questionType") or {}).get("id", 1))
        if "滑块" in str(r.get("msg") or ""):
            raise Slider(str(r.get("msg")))
        return (r.get("rt") or {}).get("statu") == "1"

    def readback_missing(self, exam_id, stu_id, qs):
        ids = ",".join(q.get("eid") for q in qs if q.get("eid"))
        info = self.h.post("/answer/getStuAnswerInfoNew",
                           {"recruitId": self.R, "examId": exam_id, "stuExamId": stu_id,
                            "schoolId": self.S, "courseId": self.C,
                            "questionIds": ids}).get("rt") or {}
        return [q for q in qs if not (info.get(q.get("eid")) or {}).get("answer")]

    def reviewed(self, exam_id, stu_id):
        """交卷后：→ [(数字id, 选项列表, 题干)]"""
        rt = self.h.post("/student/lookHomework",
                         {"recruitId": self.R, "studentExamId": stu_id, "examId": exam_id,
                          "schoolId": self.S, "courseId": self.C}).get("rt") or {}
        qs = [q for p in ((rt.get("examBase") or {}).get("workExamParts") or [])
              for q in (p.get("questionDtos") or [])]
        return [(str(q.get("id")), [o["id"] for o in (q.get("questionOptions") or [])],
                 plain(q.get("name"))) for q in qs], \
               {str(q.get("id")): (q.get("questionType") or {}).get("id", 1) for q in qs}

    def sheet(self, exam_id, stu_id, qids):
        return self.h.post("/answer/checkAnswerSheetInfo",
                           {"recruitId": self.R, "stuExamId": stu_id, "examId": exam_id,
                            "schoolId": self.S, "courseId": self.C,
                            "questionIds": ",".join(qids)}).get("rt") or {}

    # ---------- ★ 自标定 + 收割 ----------
    def calibrate(self, exam_id, items, right_qids):
        """用「答对的题」标定 sha→字母下标"""
        added = 0
        for qid, opts, _ in items:
            if str(qid) not in right_qids:
                continue
            correct = right_qids[str(qid)]
            if correct not in opts:
                continue
            p = opts.index(correct)
            for k in range(len(opts)):
                order = opts[k:] + opts[:k]
                img = self.answer_img(exam_id, qid, order)
                if not img:
                    continue
                letter_idx = (p - k) % len(opts)
                sha = hashlib.sha256(img).hexdigest()
                if self.lmap.get(sha) != letter_idx:
                    self.lmap[sha] = letter_idx
                    added += 1
        if added:
            self.save_lmap()
        return added

    def solve(self, exam_id, qid, opts, qtype):
        """返回正确选项列表；拿不到返回 None

        单选/判断：选项按原序传 → 图 = 单字母 → 直接定位
        多选：两两配对
              单字母A = 该对第一个对   单字母B = 第二个对
              组合图   = 两个都对       无图    = 两个都错
        """
        if not opts:
            return None
        if qtype != 2 or len(opts) <= 2:
            img = self.answer_img(exam_id, qid, opts)
            if not img:
                return None
            idx = self.lmap.get(hashlib.sha256(img).hexdigest())
            if idx is None or idx >= len(opts):
                return None
            return [opts[idx]]

        # 多选
        ans = []
        for i in range(0, len(opts), 2):
            pair = opts[i:i + 2]
            img = self.answer_img(exam_id, qid, pair)
            if not img:
                continue                      # 两个都错
            sha = hashlib.sha256(img).hexdigest()
            hit = None
            for li, s in self.rev.items():
                if s == sha and li < len(pair):
                    hit = li
            if hit is not None:
                ans.append(pair[hit])         # 只有一个是正确的
            else:
                ans.extend(pair)              # 组合图 = 两个都对
        return ans or None

    def harvest(self, exam_id, items, types=None):
        got = 0
        for k, (qid, opts, name) in enumerate(items):
            qt = (types or {}).get(qid, 1)
            ans = self.solve(exam_id, qid, opts, qt)
            if ans:
                self.db[hashlib.md5(name.encode()).hexdigest()] = ans
                got += 1
        if got:
            self.save_db()
        return got

    # ---------- 单份 ----------
    def one(self, x, idx, total):
        eid, sid = x["examId"], x["id"]
        name = x.get("examName") or eid
        full = float(x.get("totalScore") or 0)
        log("")
        log("─" * 78)
        log("  [%d/%d] %s   满分 %s   剩余重做 %d"
            % (idx, total, name, x.get("totalScore"), self.redo_left(eid)))
        log("─" * 78)

        for rd in range(1, max(1, self.max_rounds) + 1):
            qs = self.questions(eid, sid)
            if not qs:
                log("    取题失败")
                return x.get("score")
            log("    第 %d 轮：%d 题" % (rd, len(qs)))

            plan = []
            hit = 0
            for i, q in enumerate(qs, 1):
                opts = [o["id"] for o in (q.get("questionOptions") or [])]
                k = qkey(q)
                pick = [c for c in (self.db.get(k) or []) if c in opts]
                tag = "题库" if pick else "未知"
                if pick:
                    hit += 1
                else:
                    pick = [opts[0]] if opts else []
                plan.append((q, k, opts, pick, tag))
                if rd == 1:
                    log("      %-2d %-28s → %-16s [%s]"
                        % (i, plain(q.get("name"))[:28], pick, tag))
            log("    题库命中 %d/%d" % (hit, len(qs)))

            for q, k, opts, pick, tag in plan:
                s = ",".join(str(z) for z in pick) if len(pick) > 1 else str(pick[0])
                self.save_one(eid, sid, q, s)
                time.sleep(0.3)

            miss = self.readback_missing(eid, sid, qs)
            if miss:
                log("    ❌ %d 题未落库，拒绝交卷" % len(miss))
                return None

            r = self.h.submit(self.R, eid, sid, len(qs))
            sc = (r.get("rt") or {}).get("score")
            log("    ★ 交卷：%s / %s" % (sc, x.get("totalScore")))
            time.sleep(1.5)

            if sc is not None and full and float(sc) >= full:
                log("    ✅ 满分")
                return sc

            # ---- 诊断 + 自标定 + 收割 ----
            items, qtypes = self.reviewed(eid, sid)
            if not items:
                log("    拿不到阅卷数据，停")
                return sc
            qids = [i[0] for i in items]
            sh = self.sheet(eid, sid, qids)
            right = {}
            for i, (qid, opts, nm) in enumerate(items):
                if i < len(plan) and (sh.get(qid) or {}).get("score") not in (None, "0", 0):
                    got = plan[i][3]
                    if got:
                        right[qid] = got[0]
            log("    答对 %d 题（用于标定字母表）" % len(right))

            cal = self.calibrate(eid, items, right)
            got = self.harvest(eid, items, qtypes)
            log("    标定 +%d 条，收割 %d 题答案（题库累计 %d）" % (cal, got, len(self.db)))

            left = self.redo_left(eid)
            if left <= KEEP_REDO:
                log("    只剩 %d 次重做，按规则保留不动（至少留 %d 次给你自己）"
                    % (left, KEEP_REDO))
                return sc
            if not self.apply_redo(eid):
                log("    申请重做失败")
                return sc
            log("    已申请重做，用答案再来一轮")
            time.sleep(1.5)

        return None


def table(h, R, C):
    out = []
    for flag in (1, 2):
        r = h.post("/student/getStudentHomework",
                   {"recruitId": R, "courseId": C, "flag": flag,
                    "pageSize": 100, "pageNum": 0})
        for x in ((r.get("rt") or {}).get("studentHomeworkList") or []):
            out.append((flag, x))
    log("")
    log("=" * 68)
    log("  %-3s %-22s %-7s %-7s %-9s %s" % ("#", "作业", "得分", "满分", "剩余重做", "状态"))
    log("=" * 68)
    tot = full = 0
    for i, (flag, x) in enumerate(out, 1):
        rn = h.post("/student/redoNum", {"recruitId": R, "examId": x.get("examId")}).get("rt")
        try:
            rem = 3 - int(rn or 0)
        except Exception:
            rem = "?"
        try:
            tot += float(x.get("score") or 0)
            full += float(x.get("totalScore") or 0)
        except Exception:
            pass
        log("  %-3d %-22s %-7s %-7s %-9s %s"
            % (i, (x.get("examName") or "")[:22], x.get("score") or "-",
               x.get("totalScore") or "-", rem,
               "未做" if flag == 1 else "已完成"))
    log("=" * 68)
    log("  合计 %g / %g" % (tot, full))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recruit", required=True)
    ap.add_argument("--course", required=True)
    ap.add_argument("--school", required=True)
    ap.add_argument("--rounds", type=int, default=3, help="每份最多几轮")
    a = ap.parse_args()

    r = Runner(a.recruit, a.course, a.school, a.rounds)
    log("→ 读取作业列表…")
    hws = r.list_hw()
    if not hws:
        log("  （这门课没有作业）")
        return 0

    todo = []
    for x in hws:
        full = float(x.get("totalScore") or 0)
        sc = float(x.get("score") or 0)
        left = r.redo_left(x["examId"])
        # 未做的要做；已做未满分的，必须还剩 > KEEP_REDO 次才动
        if x.get("_flag") == 1 or (full and sc < full and left > KEEP_REDO):
            todo.append(x)

    log("  共 %d 份，需要处理 %d 份" % (len(hws), len(todo)))
    log("  （规则：每份永远保留 %d 次重做机会，脚本不碰）" % KEEP_REDO)
    if not todo:
        log("  ✅ 全部已满分")
        table(r.h, r.R, r.C)
        return 0

    try:
        for i, x in enumerate(todo, 1):
            r.one(x, i, len(todo))
    except Slider as e:
        log("")
        log("  ⚠️ 需要过滑块验证：%s" % e)
        log("     人工做一次：打开作业页 → 点一题答案 → 拖动滑块")
        log("     验证是【会话级】的，过一次整条会话就通了")
        log("     处理完重新运行本命令即可（已完成的不会重做）")
    table(r.h, r.R, r.C)
    return 0


if __name__ == "__main__":
    sys.exit(main())
