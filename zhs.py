#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树刷课 · 交互式入口
================================================================
直接运行，跟着提示走：

    python zhs.py

会自动：抓课程 → 你选序号 → 按课程类型给出可做操作 → 执行。
会话缺失或过期时会先引导扫码登录。
"""
import json
import os as _os
import subprocess
import sys
from pathlib import Path as _P

ROOT = _P(_os.environ.get("ZHS_ROOT") or _P(__file__).resolve().parent)
CAP = _P(_os.environ.get("ZHS_DATA") or (ROOT / "capture"))
INT = ROOT / "internal"
PY = sys.executable

# ★ Windows 下子进程 stdout 默认走 GBK，编不出 ✅ 这类字符。
#   这里统一强制 UTF-8，父进程也用同一编码读取。
CHILD_ENV = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

SLIDER_HINT = "滑块验证未通过"


def _setup_console():
    """Windows 控制台默认 GBK，打不出 ✅ 这类字符。切成 UTF-8。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console()

def log(*a):
    print(*a, flush=True)


def run(script, args=None):
    cmd = [PY, str(INT / script)] + [str(x) for x in (args or [])]
    return subprocess.call(cmd, cwd=str(ROOT), env=CHILD_ENV)


def run_checked(script, args=None):
    """跑子脚本；若输出里出现滑块提示，补一句人话"""
    cmd = [PY, str(INT / script)] + [str(x) for x in (args or [])]
    p = subprocess.run(cmd, cwd=str(ROOT), env=CHILD_ENV,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    print(out, end="")
    if SLIDER_HINT in out:
        log("")
        log("  ⚠️ 需要过滑块验证 —— 工具暂不处理")
        log("     人工做一次即可：打开作业页 → 点一题答案 → 拖动滑块")
        log("     验证是【会话级】的，过一次整条会话就通了（等待不会自动解除）")
        return 1
    return p.returncode


# ---------------- 课程列表 ----------------

def load_courses(refresh=False):
    f = CAP / "my_courses.json"
    if refresh or not f.exists():
        log("→ 正在获取课程列表…")
        subprocess.run([PY, str(INT / "_课程总览.py")], cwd=str(ROOT),
                       env=CHILD_ENV,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("")
    if not f.exists():
        raise SystemExit("❌ 拿不到课程列表，先跑 `python zhs.py login`")
    return json.loads(f.read_text(encoding="utf-8"))


def flat(courses):
    return ([("share", x) for x in (courses.get("share") or [])] +
            [("ai", x) for x in (courses.get("ai") or [])])


def show(courses):
    share = courses.get("share") or []
    ai = courses.get("ai") or []
    log("=" * 88)
    log("  共享课")
    log("=" * 88)
    if not share:
        log("  （无）")
    for i, x in enumerate(share, 1):
        log("  [%d] %-26s 进度 %-8s  %s" % (i, x.get("name"), x.get("progress"), x.get("teacher")))
    log("")
    log("=" * 88)
    log("  新形态课程（AI 智慧课）")
    log("=" * 88)
    if not ai:
        log("  （无）")
    for i, x in enumerate(ai, len(share) + 1):
        log("  [%d] %-26s 学习 %-5s 掌握 %-5s  %s"
            % (i, x.get("name"), x.get("schedule"), x.get("mastery"), x.get("teacher")))
    log("")


def pick(courses, key):
    items = flat(courses)
    if not items:
        raise SystemExit("❌ 没有课程")
    if key and str(key).isdigit():
        n = int(key)
        if 1 <= n <= len(items):
            return items[n - 1]
        raise SystemExit("❌ 序号 %d 超范围（1-%d）" % (n, len(items)))
    if key:
        hit = [(k, r) for k, r in items if str(key).lower() in (r.get("name") or "").lower()]
        if len(hit) == 1:
            return hit[0]
        if not hit:
            raise SystemExit("❌ 没有匹配「%s」的课程" % key)
        log("⚠️ 「%s」匹配到 %d 门，请用序号：" % (key, len(hit)))
        show(courses)
        raise SystemExit(1)
    if len(items) == 1:
        return items[0]
    log("⚠️ 有多门课，请指定序号：")
    show(courses)
    raise SystemExit(1)


# ---------------- 各动作 ----------------

def do_study(r, a):
    args = ["--course", r["courseId"], "--class", r["classId"]]
    if a.get("nodes"):
        args += ["--nodes", a["nodes"]]
    if a.get("list"):
        args += ["--list"]
    return run("_新课程_学习进度.py", args)


def do_exam(r, a):
    args = ["--course", r["courseId"], "--class", r["classId"],
            "--rounds", a.get("rounds") or 6]
    if a.get("nodes"):
        args += ["--nodes", a["nodes"]]
    if a.get("list"):
        args += ["--list"]
    return run("_新课程_考试掌握度.py", args)


def do_video(r, a):
    args = []
    if a.get("limit"):
        args += ["--limit", a["limit"]]
    return run("_共享课_视频.py", args)


def do_checkin(r, a):
    args = ["--status"] if a.get("list") else []
    return run("_共享课_每日习惯分.py", args)


def do_hw(r, a):
    """自动刷作业：取题 → 答题 → 交卷 → 看错题 → 重做，最后出总表"""
    args = ["--recruit", r.get("recruitId"), "--course", r.get("courseId"),
            "--school", r.get("schoolId") or 369]
    if a.get("rounds"):
        args += ["--rounds", a["rounds"]]
    return run_checked("_共享课_自动作业.py", args)


def do_all_ai(r, a):
    rc = 0
    log("\n########## ① 学习进度 ##########")
    rc |= do_study(r, a)
    log("\n########## ② 考试掌握度 ##########")
    rc |= do_exam(r, a)
    return rc


def do_all_share(r, a):
    """全刷 = 视频 + 作业。刷的过程本身就算当天签到，不用单独跑签到"""
    rc = 0
    log("\n########## ① 视频 ##########")
    rc |= do_video(r, a)
    log("\n########## ② 作业 ##########")
    rc |= do_hw(r, a)
    return rc


MENU = {
    "ai": [
        ("刷学习进度",               do_study),
        ("刷考试掌握度",             do_exam),
        ("全刷（学习 + 掌握）",       do_all_ai),
    ],
    "share": [
        ("刷视频",                   do_video),
        ("刷作业（自动）",             do_hw),
        ("每日签到（规律学习分）",     do_checkin),
        ("全刷（视频 + 作业）",       do_all_share),
    ],
}


def session_alive():
    """试抓一次课程列表，判断会话是否可用"""
    r = subprocess.run([PY, str(INT / "_课程总览.py")], cwd=str(ROOT),
                       env=CHILD_ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.returncode == 0 and (CAP / "my_courses.json").exists())


def ensure_session():
    """没有可用会话就先登录"""
    if session_alive():
        return True
    log("  ⚠️ 没有可用会话（或已过期）")
    log("")
    ans = input("  现在扫码登录？(Y/n): ").strip().lower()
    if ans in ("n", "no"):
        return False
    run("_共享课_登录.py")
    log("")
    if not session_alive():
        log("  登录后仍拿不到课程列表，先看自检：")
        log("")
        act_doctor()
        return False
    return True


def interactive(courses):
    items = flat(courses)
    if not items:
        raise SystemExit("❌ 没有课程")
    show(courses)
    while True:
        try:
            s = input("选择课程序号（q 退出）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return 0
        if s.lower() in ("q", "quit", "exit"):
            return 0
        if s.isdigit() and 1 <= int(s) <= len(items):
            kind, r = items[int(s) - 1]
            break
        log("  请输入 1-%d 之间的序号" % len(items))

    log("")
    log("  已选：%s  [%s]" % (r.get("name"), "新形态课" if kind == "ai" else "共享课"))
    opts = MENU[kind]
    log("")
    for i, (lbl, _) in enumerate(opts, 1):
        log("  [%d] %s" % (i, lbl))
    log("")
    while True:
        try:
            s = input("选择操作（q 退出）: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); return 0
        if s in ("q", "quit", "exit"):
            return 0
        if s.isdigit() and 1 <= int(s) <= len(opts):
            return opts[int(s) - 1][1](r, {})
        log("  请输入 1-%d" % len(opts))


def act_doctor():
    log("=" * 70)
    log("  环境自检")
    log("=" * 70)
    ok = True
    for mod in ("Crypto", "curl_cffi"):
        try:
            __import__(mod)
            log("  ✅ %-12s 已安装" % mod)
        except Exception:
            log("  ❌ %-12s 未安装 →  pip install -r requirements.txt" % mod)
            ok = False
    f = CAP / "session.json"
    if f.exists():
        have = json.loads(f.read_text(encoding="utf-8"))
        log("  ✅ session.json  含 %d 个域" % len(have))
        for d in ("onlineservice-api.zhihuishu.com", "studentexam-api.zhihuishu.com",
                  "studyservice-api.zhihuishu.com"):
            log("       %s %s" % ("✅" if d in have else "⭕", d))
    else:
        log("  ❌ session.json 不存在 →  python zhs.py login")
        ok = False
    log("")
    log("  结论：%s" % ("可以开刷" if ok else "先补齐上面的问题"))
    return 0 if ok else 1


def main():
    log("")
    log("=" * 70)
    log("  智慧树刷课")
    log("=" * 70)
    log("")

    if not ensure_session():
        log("  ❌ 没有可用会话，无法继续")
        return 1

    courses = load_courses(refresh=True)
    return interactive(courses)


if __name__ == "__main__":
    sys.exit(main())
