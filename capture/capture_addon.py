"""
mitmproxy 抓包插件 —— 记录指定后端的完整请求/响应（含 body）

诊断增强：所有经过代理的请求都会在 stdout 打印一行 [REQ] host，
          便于确认代理是否真的在工作。
"""
import json
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "proxy-flow.jsonl"
DIAG = Path(__file__).resolve().parent / "proxy-hosts.log"

KEEP_HOSTS = (
    # 全站捕获（静态资源靠 SKIP_EXT 过滤）
    ".zhihuishu.com", ".polymas.com", ".163.com", ".163yun.com",
    ".126.net", ".253.com", ".gtimg.com", ".qq.com",
    ".aliyuncs.com", ".zhihuishu.cn",
)
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
            ".woff2", ".ttf", ".ico", ".mp4", ".m3u8", ".ts", ".map", ".webp")


def _interesting(flow) -> bool:
    host = flow.request.host
    if not any(h in host for h in KEEP_HOSTS):
        return False
    path = flow.request.path.split("?")[0].lower()
    if path.endswith(SKIP_EXT):
        return False
    return True


def _body(b) -> str:
    if not b:
        return ""
    try:
        t = b.decode("utf-8")
        return t if len(t) < 20000 else t[:20000] + "...[截断]"
    except Exception:
        return "<binary %d bytes>" % len(b)


class Cap:
    def __init__(self):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        self.fh = OUT.open("a", encoding="utf-8")
        self.dg = DIAG.open("a", encoding="utf-8")
        self.n = 0
        self.seen = 0
        print("[capture] 业务日志 -> %s" % OUT, flush=True)
        print("[capture] 域名诊断 -> %s" % DIAG, flush=True)

    def request(self, flow):
        flow._t0 = time.time()
        self.seen += 1
        try:
            line = "%s %s%s" % (flow.request.method, flow.request.host, flow.request.path[:120])
            self.dg.write(line + "\n")
            self.dg.flush()
        except Exception:
            pass
        if self.seen <= 40 or self.seen % 100 == 0:
            print("[REQ#%d] %s" % (self.seen, line), flush=True)
        flow._skip = not _interesting(flow)

    def response(self, flow):
        if getattr(flow, "_skip", True):
            return
        try:
            req, res = flow.request, flow.response
            rec = {
                "t": round(getattr(flow, "_t0", 0), 3),
                "method": req.method,
                "url": req.pretty_url,
                "reqHeaders": [[k, v] for k, v in req.headers.items()],
                "reqBody": _body(req.content),
                "status": res.status_code,
                "resHeaders": [[k, v] for k, v in res.headers.items()],
                "resBody": _body(res.content),
            }
            self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.fh.flush()
            self.n += 1
            print("[CAP] #%d %s %s" % (self.n, req.method, req.path[:90]), flush=True)
        except Exception as e:
            print("[capture] 记录失败: %s" % e, flush=True)

    def done(self):
        self.fh.close()
        self.dg.close()


addons = [Cap()]
