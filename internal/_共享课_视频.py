#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树共享课 · 全自动刷课（curl_cffi 版 / Chrome 指纹）

严格照抄真实客户端时间线（反混淆源码实测）：
  每 1990ms   采样 watchPoint          (learningTimeRecord)
  每 4990ms   totalStudyTime += 5×倍速  (totalStudyTimeFun)
  每 300000ms 上报一次                 (databaseInterval)
  完成阈值 81%                          (0x51)

关键安全约束（实测教训）：
  * 两次上报间隔必须 >= 300 秒（曾因 12 秒连发触发易盾 -12）
  * totalStudyTime 不得超过 videoSec
  * 撞到 -12 立即停止并提醒（不要连续硬试，会加重风控）

用法:
  python tools/zhs_auto.py <RAC> [--speed=1.8] [--only=videoId] [--limit=N] [--dry]
"""
import os as _os
from pathlib import Path as _P
ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent.parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
CAP.mkdir(parents=True, exist_ok=True)

import argparse
import base64
import json
import math
import sys
import time
from pathlib import Path

from Crypto.Cipher import AES
from curl_cffi import requests as cr

# ---------------- 常量 ----------------
SESSION_FILE = CAP / "session.json"
STATE_FILE = CAP / "progress_py.json"
API = "studyservice-api.zhihuishu.com"

IV = b"1g3qqdh4jvbskb9x"
VIDEO_KEY = b"azp53h0kft7qi78q"
EV_KEY = "zzpttjd"

TICK_WP = 1.99        # watchPoint 采样周期（前端 0x7c6）
TICK_STUDY = 4.99     # 时长累加周期（前端 0x137e）
REPORT_EVERY = 300.0  # 上报周期（前端 0x493e0）
DONE_PCT = 0.81       # 服务端完成阈值（0x51）
MIN_GAP = 60.0        # 两次上报最小间隔（防连发；真实客户端在完成时会强制冲刷）
IMPERSONATE = "chrome"


def log(*a):
    print(*a, flush=True)


def hhmmss(s):
    s = int(max(0, s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ---------------- 密码学 ----------------
def _pad(d):
    n = 16 - len(d) % 16
    return d + bytes([n]) * n


def aes_enc(obj):
    c = AES.new(VIDEO_KEY, AES.MODE_CBC, IV)
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(c.encrypt(_pad(raw))).decode()


def ev_encode(arr):
    s = ";".join(str(x) for x in arr)
    out = []
    for i, ch in enumerate(s):
        n = ord(ch) ^ ord(EV_KEY[i % len(EV_KEY)])
        h = format(n, "x")
        if len(h) < 2:
            h = "0" + h
        out.append(h[-4:])
    return "".join(out)


def stamp():
    return int(time.time()) * 1000


class WatchPoint:
    """前端 watchPointPost：初始 0,1；之后每次追加 floor(totalStudyTime/5)+2"""
    def __init__(self, init=0):
        self.reset(init)

    def reset(self, t=0):
        self.wp = ["0", "1"]

    def add(self, t):
        self.wp.append(str(int(max(0, t) // 5) + 2))

    def get(self):
        return ",".join(self.wp)


# ---------------- HTTP ----------------
class Zhs:
    def __init__(self, uuid):
        self.uuid = uuid
        d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))[API]
        self.s = cr.Session(impersonate=IMPERSONATE, timeout=25)
        self.s.headers.update({
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": d.get("ua") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
            "Origin": "https://studyvideoh5.zhihuishu.com",
            "Referer": "https://studyvideoh5.zhihuishu.com/",
            "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })
        for part in d["cookie"].split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                self.s.cookies.set(k.strip(), v.strip(), domain=API)

    def api(self, path, data, method="POST"):
        t = stamp()
        dd = dict(data)
        dd["dateFormate"] = t
        form = {"secretStr": aes_enc(dd), "dateFormate": str(t)}
        url = f"https://{API}{path}"
        try:
            r = self.s.post(url, data=form) if method == "POST" else self.s.get(url, params=form)
        except Exception as e:
            return {"code": -999, "message": f"网络异常 {e}"}
        try:
            return json.loads(r.text)
        except Exception:
            return {"code": -998, "raw": r.text[:300]}


# ---------------- 业务 ----------------
def build_ctx(z, RAC):
    c = z.api("/gateway/t/v1/learning/queryCourse", {"recruitAndCourseId": RAC})
    if c.get("code") != 0:
        raise SystemExit("queryCourse 失败: " + json.dumps(c, ensure_ascii=False)[:300])
    vids = z.api("/gateway/t/v1/learning/videolist", {"recruitAndCourseId": RAC})
    if vids.get("code") != 0:
        raise SystemExit("videolist 失败: " + json.dumps(vids, ensure_ascii=False)[:300])
    videos, order, lesson_ids, video_ids = {}, [], [], []
    for ch in vids["data"]["videoChapterDtos"]:
        for l in ch.get("videoLessons", []):
            lesson_ids.append(l["id"])
            smalls = l.get("videoSmallLessons") or ([{**l, "id": 0}] if l.get("videoId") else [])
            for v in smalls:
                o = {"videoId": v["videoId"], "lessonId": l["id"], "smallLessonId": v.get("id", 0),
                     "chapterId": ch["id"], "name": v.get("name"), "videoSec": v.get("videoSec")}
                videos[str(v["videoId"])] = o
                order.append(str(v["videoId"]))
                if v.get("id"):
                    video_ids.append(v["id"])
    return {"RAC": RAC, "recruitId": c["data"]["recruitId"],
            "courseId": c["data"]["courseInfo"]["courseId"],
            "courseName": c["data"]["courseInfo"].get("name"),
            "classId": c["data"].get("classId"),
            "canJumpChapter": c["data"]["courseInfo"].get("canJumpChapter"),
            "hasHabbitScore": c["data"].get("hasHabbitScore"),
            "studyStatus": c["data"].get("studyStatus", "0"),
            "videos": videos, "order": order, "lessonIds": lesson_ids, "videoIds": video_ids}


def get_states(z, ctx):
    r = z.api("/gateway/t/v1/learning/queryStuyInfo",
              {"lessonIds": ctx["lessonIds"], "lessonVideoIds": ctx["videoIds"], "recruitId": ctx["recruitId"]})
    return r.get("data") or {} if r.get("code") == 0 else {}


def state_of(st, v):
    return (st.get("lv") or {}).get(str(v["smallLessonId"])) or \
           (st.get("lesson") or {}).get(str(v["lessonId"])) or {}


def token_for(z, ctx, v):
    r = z.api("/gateway/t/v1/learning/prelearningNote", {
        "ccCourseId": ctx["courseId"], "chapterId": v["chapterId"], "isApply": 1,
        "lessonId": v["lessonId"], "lessonVideoId": v["smallLessonId"],
        "recruitId": ctx["recruitId"], "videoId": v["videoId"]})
    return ((r.get("data") or {}).get("studiedLessonDto") or {}).get("id")


def report(z, ctx, v, total_study, play_times, wp):
    token = token_for(z, ctx, v)
    if not token:
        log("    ⚠️ 拿不到 learningTokenId，跳过本次上报")
        return None
    ev = [ctx["recruitId"], v["lessonId"], v["smallLessonId"], v["videoId"], v["chapterId"],
          ctx["studyStatus"], int(round(play_times)), int(round(total_study)),
          int(round(total_study)), z.uuid + "zhs"]
    payload = {"ewssw": wp.get(), "sdsew": ev_encode(ev),
               "zwsds": base64.b64encode(str(token).encode()).decode(),
               "courseId": ctx["courseId"]}
    r = z.api("/gateway/t/v1/learning/saveDatabaseIntervalTimeV2", payload)
    code = r.get("code")
    if code == 0:
        log(f"    ✅ 上报 totalStudyTime={int(total_study)} playTimes={int(play_times)} → code=0 submitSuccess")
    else:
        log(f"    ❌ 上报 totalStudyTime={int(total_study)} playTimes={int(play_times)} → code={code} {r.get('message')}")
    return r


def handle_quizzes(z, ctx, v, pointers, done, cur):
    for q in pointers:
        key = str(q["questionIds"])
        if key in done or cur < q["timeSec"]:
            continue
        r = z.api("/gateway/t/v1/popupAnswer/lessonPopupExam", {
            "lessonId": v["lessonId"], "lessonVideoId": v["smallLessonId"], "questionIds": key})
        if r.get("code") != 0:
            log(f"\n    ❌ 取弹题失败 code={r.get('code')}")
            done.add(key)
            continue
        try:
            qd = r["data"]["lessonTestQuestionUseInterfaceDtos"][0]["testQuestion"]
        except Exception:
            log("\n    ⚠️ 弹题结构异常")
            done.add(key)
            continue
        answers = ",".join(str(o["id"]) for o in qd.get("questionOptions", []) if str(o.get("result")) == "1")
        sub = z.api("/gateway/t/v1/popupAnswer/saveLessonPopupExamSaveAnswer", {
            "courseId": ctx["courseId"], "recruitId": ctx["recruitId"],
            "testQuestionId": qd["questionId"], "isCurrent": "1",
            "lessonId": v["lessonId"], "lessonVideoId": v["smallLessonId"],
            "answer": answers, "testType": 0})
        ok = "✅" if sub.get("code") == 0 else "❌"
        log(f"\n    {ok} 弹题 t={q['timeSec']}s [{qd.get('questionType', {}).get('name', '')}] "
            f"作答 {answers} → code={sub.get('code')}")
        done.add(key)


def save_state(RAC, vid, study, sec):
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        s = {}
    s.setdefault(RAC, {})[vid] = {"studyTotalTime": int(study), "videoSec": sec,
                                  "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def watch_video(z, ctx, v, st, speed, target_pct, dry):
    cur = state_of(st, v)
    start = int(cur.get("studyTotalTime") or 0)
    sec = v["videoSec"]
    need = math.ceil(sec * target_pct)
    done_need = math.ceil(sec * DONE_PCT)
    log(f"\n▶ {v['name']}  [{v['videoId']}]  videoSec={sec}  已有={start}s ({start / sec * 100:.1f}%)")
    if cur.get("watchState") == 1 and start >= done_need:
        log("  已看完，跳过")
        return "ok"

    ptr = z.api("/gateway/t/v1/popupAnswer/loadVideoPointerInfo", {
        "lessonId": v["lessonId"], "lessonVideoId": v["smallLessonId"],
        "recruitId": ctx["recruitId"], "courseId": ctx["courseId"]})
    pointers = sorted((ptr.get("data") or {}).get("questionPoint") or [], key=lambda x: x["timeSec"])
    log(f"  弹题数: {len(pointers)}" + (f"  时间点: {', '.join(str(p['timeSec']) + 's' for p in pointers)}" if pointers else ""))

    if dry:
        log("  [dry] 不执行时间线")
        return "ok"

    done = set()
    wp = WatchPoint(start)
    study = float(start)
    play = 0.0
    since_study = 0.0
    last_report = time.time()
    last_reported_study = float(start)
    t0 = time.time()
    real_total = max(1.0, (need - start) / speed)

    while study < need:
        time.sleep(TICK_WP)
        since_study += TICK_WP
        while since_study >= TICK_STUDY:
            study += 5 * speed
            play += 5 * speed
            since_study -= TICK_STUDY
        if study > sec:
            play -= (study - sec)
            study = float(sec)

        wp.add(study)
        if pointers:
            handle_quizzes(z, ctx, v, pointers, done, study)

        gap = time.time() - last_report
        want_final = study >= need and gap >= MIN_GAP
        if gap >= REPORT_EVERY or want_final:
            r = report(z, ctx, v, study, play, wp)
            play = 0.0
            wp.reset(study)
            last_report = time.time()
            last_reported_study = study
            if r is not None:
                code = r.get("code")
                if code == -12:
                    log("\n  🛑 命中易盾验证码（-12）。已停止，避免加重风控。")
                    log("     → 请在浏览器里打开该视频播放几分钟，让会话恢复后再继续。")
                    return "captcha"
                if code in (-9, -10, 403):
                    log(f"\n  🛑 账号级问题 code={code}，停止整个任务。")
                    return "fatal"
                if code != 0:
                    log(f"\n  🛑 上报返回 code={code}，停止。")
                    return "fatal"

        el = time.time() - t0
        sys.stdout.write(f"\r  {hhmmss(study)}/{hhmmss(need)}  ({study / sec * 100:.1f}%)  "
                         f"×{speed}  实时 {hhmmss(el)}/{hhmmss(real_total)}   ")
        sys.stdout.flush()
        save_state(ctx["RAC"], v["videoId"], study, sec)

    print()
    # 循环结束 → 只在「尚未达标」时才补最终上报。
    # 若中途上报已把进度推过 81%（服务端已视为完成），补报只多刷几十秒，
    # 却要多发一次请求、抬高累积风险分（实测连续补报第 3 次触发易盾 -12）。
    if last_reported_study >= done_need:
        log(f"  已达标（{study / sec * 100:.1f}%，上次上报 {int(last_reported_study)}s ≥ 81% 阈值），跳过补报以降低风险。")
        return "ok"
    if abs(study - last_reported_study) < 1:
        log("  最终值已上报过，跳过补报。")
        return "ok"
    gap = time.time() - last_report
    if gap < MIN_GAP:
        log(f"  等待 {MIN_GAP - gap:.0f}s 后补最终上报…")
        time.sleep(MIN_GAP - gap)
    r = report(z, ctx, v, study, play, wp)
    if r is not None:
        code = r.get("code")
        if code == -12:
            log("\n  🛑 命中易盾验证码（-12）。已停止，避免加重风控。")
            log("     → 请在浏览器里打开该视频播放几分钟，让会话恢复后再继续。")
            return "captcha"
        if code in (-9, -10, 403):
            log(f"\n  🛑 账号级问题 code={code}，停止整个任务。")
            return "fatal"
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("RAC")
    ap.add_argument("--speed", type=float, default=1.8)
    ap.add_argument("--only", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pct", type=float, default=1.0)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    sess = json.loads(SESSION_FILE.read_text(encoding="utf-8"))[API]
    import re
    m = re.search(r"(?:^|;\s*)CASLOGC=([^;]+)", sess["cookie"])
    uuid = json.loads(__import__("urllib.parse", fromlist=["unquote"]).unquote(m.group(1)))["uuid"]

    z = Zhs(uuid)
    ctx = build_ctx(z, a.RAC)
    total = sum(ctx["videos"][i]["videoSec"] or 0 for i in ctx["order"])
    log(f"课程: {ctx['courseName']}")
    log(f"recruitId={ctx['recruitId']}  courseId={ctx['courseId']}  uuid={uuid}")
    log(f"倍速={a.speed}x  上报周期={REPORT_EVERY:.0f}s  最小间隔={MIN_GAP:.0f}s  "
        f"canJumpChapter={ctx['canJumpChapter']}  hasHabbitScore={ctx['hasHabbitScore']}")
    log(f"总视频 {len(ctx['order'])} 个，总时长 {hhmmss(total)}（{a.speed}x 约需 {hhmmss(total / a.speed)}）")

    st = get_states(z, ctx)
    lst = [ctx["videos"][i] for i in ctx["order"]]
    if a.only:
        lst = [v for v in lst if str(v["videoId"]) == str(a.only)]
    if a.limit:
        lst = lst[:a.limit]

    log("\n待处理:")
    for v in lst:
        s = state_of(st, v)
        log(f"  {v['videoId']}  {str(v['name'])[:24]:<26} {str(s.get('studyTotalTime') or 0):>5}/"
            f"{str(v['videoSec']):>5}s  watchState={s.get('watchState')}")

    for v in lst:
        st = get_states(z, ctx)
        r = watch_video(z, ctx, v, st, a.speed, a.pct, a.dry)
        if r == "fatal":
            log("中止。")
            break
        if r == "captcha":
            log("已停止（需人工处理易盾）。")
            break

    log("\n═══ 最终状态 ═══")
    st = get_states(z, ctx)
    done = 0
    for i in ctx["order"]:
        v = ctx["videos"][i]
        s = state_of(st, v)
        if s.get("watchState") == 1:
            done += 1
        log(f"  {'✅' if s.get('watchState') == 1 else '  '} {str(v['name'])[:24]:<26} "
            f"{str(s.get('studyTotalTime') or 0):>5}/{str(v['videoSec']):>5}s  "
            f"({((s.get('studyTotalTime') or 0) / v['videoSec'] * 100):.1f}%)")
    log(f"\n完成 {done}/{len(ctx['order'])}")


if __name__ == "__main__":
    main()
