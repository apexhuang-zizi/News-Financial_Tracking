# -*- coding: utf-8 -*-
"""
Kickstarter 每日科技众筹 Top5 —— News-Financial_Tracking 集成版

与仓库主引擎（scraper.py + .github/workflows/main.yml）共用同一次定时运行、
同一次 git 提交、同一次 GitHub Pages 部署。本脚本只依赖 Python 标准库 + curl，
不写入任何第三方 python 包依赖，也不会修改 scraper.py 的任何抓取/还原逻辑。

产出：
    ks_data.json        最近 KEEP_DAYS 天的完整快照（自动裁剪，体积有上界）
    ks_history.csv      累计历史台账（append-only，与仓库 history.csv 风格一致）
    kickstarter.html    每日看板页面（今日全量 + 近 7 天切换 + 累计历史榜）

用法：
    python kickstarter_scraper.py          # 抓取并重建页面
    python kickstarter_scraper.py --dry    # 只抓取，不写文件

容错原则：
    抓取整体失败时【不会覆盖】已有的 kickstarter.html，保持昨日页面可访问。
"""
import argparse
import csv
import html as htmllib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(BASE, "ks_data.json")
HIST_CSV = os.path.join(BASE, "ks_history.csv")
OUT_HTML = os.path.join(BASE, "kickstarter.html")
COOKIE = os.path.join(BASE, ".ks_cookie.txt")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

TOP_N = 5
KEEP_DAYS = 7          # 完整快照保留天数（自动裁剪，防止仓库膨胀）
ARCHIVE_DAYS = 90      # 页面底部历史榜展示天数（数据源为累计 CSV）
TZ = timezone(timedelta(hours=7))


def _find_curl():
    """跨平台定位 curl：CI(Linux) 用 PATH，Windows 回落到 System32"""
    p = shutil.which("curl")
    if p:
        return p
    win = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
    return win if os.path.exists(win) else "curl"


CURL = _find_curl()

# ---- 抓取来源：(描述, query串, referer) ----
SOURCES = [
    ("Hardware 热门", "category_id=52&sort=popularity",
     "https://www.kickstarter.com/discover/categories/technology/hardware"),
    ("Technology 热门", "category_id=16&sort=popularity",
     "https://www.kickstarter.com/discover/categories/technology"),
    ("AI 关键词", "term=AI&sort=popularity&category_id=16",
     "https://www.kickstarter.com/discover/categories/technology"),
    ("机器人/自动化", "term=robot%20automation&sort=popularity&category_id=16",
     "https://www.kickstarter.com/discover/categories/technology"),
    ("软件/APP", "term=software%20app&sort=popularity&category_id=16",
     "https://www.kickstarter.com/discover/categories/technology"),
]

# 领域关键词加权（命中越多越优先）
KEYWORDS = [
    ("ai", 0.16), ("artificial intelligence", 0.16), ("llm", 0.16), ("gpt", 0.12),
    ("robot", 0.14), ("robotic", 0.14), ("automation", 0.14), ("automate", 0.12),
    ("smart", 0.08), ("sensor", 0.08), ("homelab", 0.10), ("home lab", 0.10),
    ("mechanical keyboard", 0.04), ("esp32", 0.08), ("raspberry", 0.06),
    ("FPGA", 0.06), ("3d print", 0.04), ("gesture", 0.05), ("voice", 0.04),
    ("solar", 0.03), ("nas", 0.05), ("seedbox", 0.02), ("e-ink", 0.06),
    ("wearable", 0.06), ("drone", 0.06), ("camera", 0.03), ("mini pc", 0.08),
]


def log(msg):
    print("[%s] %s" % (datetime.now(TZ).strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 网络层
def _proxy_url():
    """复用仓库已有的 PROXY_URL 密钥。

    GitHub Actions 的机房 IP 会被 Cloudflare 直接拒绝（返回空响应），
    走仓库为其他爬虫配好的代理才能拿到数据。
    """
    for k in ("MY_PROXY_URL", "PROXY_URL", "HTTPS_PROXY", "https_proxy"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def _curl_once(url, referer, accept, extra, tries):
    body, code = "", 0
    for attempt in range(tries):
        cmd = [CURL, "-sSL", "--compressed", "-m", "90", "-A", UA,
               "-c", COOKIE, "-b", COOKIE,
               "-H", "Referer: " + referer,
               "-H", "Accept-Language: en-US,en;q=0.9",
               "-w", "\n__HTTPCODE__%{http_code}"] + list(extra)
        if accept:
            cmd += ["-H", "Accept: " + accept]
        cmd.append(url)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stdout
        except Exception as e:
            log("curl 执行异常: %s" % e)
            time.sleep(3 + attempt * 4)
            continue
        if "__HTTPCODE__" in res:
            tail, code_s = res.rsplit("__HTTPCODE__", 1)
            try:
                code = int(code_s.strip() or 0)
            except ValueError:
                code = 0
            if code == 200 and tail.strip():
                return tail, code
            body = tail
        time.sleep(3 + attempt * 4)
    return body, code


JINA = "https://r.jina.ai/"
CATEGORY_REF = "https://www.kickstarter.com/discover/categories/technology/hardware"


def _strip_jina(text):
    """r.jina.ai 会在正文前加 Title / URL Source / Markdown Content 头，需要剥掉"""
    m = re.search(r"Markdown Content:\s*", text)
    return text[m.end():] if m else text


def fetch_text(url, referer, accept=None, tries=2, allow_jina=True):
    """多通道降级：代理 -> 直连 -> r.jina.ai 渲染。返回 (正文, 通道名)"""
    errors = []
    plans = []
    px = _proxy_url()
    if px:
        plans.append(("proxy", ["-x", px]))
    plans.append(("direct", []))
    for label, extra in plans:
        body, code = _curl_once(url, referer, accept, extra, tries)
        if code == 200 and body.strip():
            return body, label
        errors.append("%s(HTTP%s,%dB)" % (label, code, len(body or "")))
    if allow_jina:
        body, code = _curl_once(JINA + url, referer, None, [], tries)
        if code == 200 and body.strip():
            return _strip_jina(body), "jina"
        errors.append("jina(HTTP%s,%dB)" % (code, len(body or "")))
    raise RuntimeError("全部通道失败: " + " / ".join(errors))


def fetch_json(url, referer, accept=None, tries=2):
    body, via = fetch_text(url, referer,
                           accept or "application/json, text/plain, */*", tries)
    try:
        return json.loads(body), via
    except Exception:
        m = re.search(r"[\[{]", body)
        if m:
            try:
                return json.loads(body[m.start():]), via
            except Exception:
                pass
        raise RuntimeError("JSON 解析失败 via %s: %r"
                           % (via, body[:120].replace("\n", " ")))


# ---------------------------------------------------------------- escaped JSON 提取
def scan_balance(raw, start):
    depth, i, n = 0, start, len(raw)
    while i < n:
        if raw.startswith("&quot;", i):
            j = raw.find("&quot;", i + 6)
            if j < 0:
                return -1
            i = j + 6
            continue
        c = raw[i]
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_items(raw, start):
    items, depth, last = [], 0, start + 1
    i, n = start + 1, len(raw)
    while i < n:
        if raw.startswith("&quot;", i):
            j = raw.find("&quot;", i + 6)
            if j < 0:
                break
            i = j + 6
            continue
        c = raw[i]
        if c in "[{":
            if depth == 0:
                last = i
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                items.append(raw[last:i + 1])
                if raw[i] == "]":
                    break
        i += 1
    return items


def loads_lenient(s):
    try:
        return json.loads(s)
    except Exception:
        pass
    for fix in (lambda t: t.replace(r'\\"', r'\\\"'),
                lambda t: t.replace(r'\\"', "'"),
                lambda t: re.sub(r'\\+"', '"', t)):
        try:
            return json.loads(fix(s))
        except Exception:
            continue
    return None


def grab_array(raw, anchor):
    """在 html-escaped JSON 文本里按 key 抓数组，逐项容错解析"""
    pos = 0
    while True:
        p = raw.find(anchor, pos)
        if p < 0:
            return []
        i = p + len(anchor)
        while i < len(raw) and raw[i] in " \n\r":
            i += 1
        if raw[i] == "[":
            out = []
            for frag in split_items(raw, i):
                obj = loads_lenient(htmllib.unescape(frag))
                if isinstance(obj, dict):
                    out.append(obj)
            if out:
                return out
        pos = p + 1


# ---------------------------------------------------------------- 数据获取
def collect_candidates():
    pool = {}
    for label, query, ref in SOURCES:
        url = "https://www.kickstarter.com/discover/advanced?%s&format=json" % query
        try:
            data, via = fetch_json(url, ref)
        except Exception as e:
            log("来源 <%s> 失败: %s" % (label, e))
            continue
        projects = data.get("projects", [])
        log("来源 <%s> 返回 %d 个项目（通道：%s）" % (label, len(projects), via))
        for rank, p in enumerate(projects):
            pid = p.get("id")
            if not pid or p.get("state") != "live":
                continue
            prev = pool.get(pid)
            s = 1.0 / (rank + 1)
            if prev is None:
                p["_sources"] = [label]
                p["_rankScore"] = s
                pool[pid] = p
            else:
                prev["_sources"].append(label)
                prev["_rankScore"] = max(prev["_rankScore"], s)
        time.sleep(2)
    return list(pool.values())


def score(p):
    pledged = float(p.get("usd_pledged") or 0)
    backers = float(p.get("backers_count") or 0)
    f_pledged = math.log10(pledged + 10) / 8.0
    f_backers = math.log10(backers + 10) / 6.0
    text = ((p.get("name") or "") + " " + (p.get("blurb") or "")).lower()
    bonus, hits = 0.0, []
    for kw, w in KEYWORDS:
        if kw.lower() in text:
            bonus += w
            hits.append(kw)
    bonus = min(bonus, 0.45)
    s = 0.33 * f_pledged + 0.27 * f_backers + 0.25 * p.get("_rankScore", 0) + bonus
    p["_kwHits"] = hits
    p["_score"] = round(s, 4)
    return s


def enrich(p):
    """抓详情页补齐档次数据；封面一律走 KS CDN 外链，不落本地（避免仓库膨胀）"""
    url = (p.get("urls") or {}).get("web", {}).get("project")
    if not url:
        return
    time.sleep(2)
    # 详情页必须拿原始 HTML（Jina 的 markdown 里没有档次数据），因此 allow_jina=False
    try:
        raw, via = fetch_text(url, CATEGORY_REF, tries=2, allow_jina=False)
    except Exception as e:
        log("  ⚠ 详情页失败: %s —— %s" % ((p.get("name") or "")[:36], e))
        return

    clean = []
    for r in grab_array(raw, "&quot;rewards&quot;:"):
        try:
            price = float(r.get("minimum") or 0)
        except Exception:
            price = 0
        if price <= 0:
            continue
        clean.append({
            "id": r.get("id"),
            "price": price,
            "title": (r.get("title") or r.get("reward") or "").strip(),
            "desc": (r.get("description") or "").strip()[:600],
            "backers": r.get("backers_count"),
            "limit": r.get("limit"),
            "remaining": r.get("remaining"),
            "eta": r.get("estimated_delivery_on"),
            "shipping": r.get("shipping_preference"),
        })
    clean.sort(key=lambda x: x["price"])
    p["rewards"] = clean
    log("  ✓ 档次 %d 个" % len(clean))

    video = p.get("video") or {}
    if isinstance(video, dict):
        p["videoMp4"] = video.get("high")
        p["videoHls"] = video.get("hls")
    for k in ("videoMp4", "videoHls"):
        v = p.get(k)
        if isinstance(v, str) and v.startswith("//"):
            p[k] = "https:" + v


def simplify(p):
    goal = float(p.get("goal") or 0)
    rate = float(p.get("static_usd_rate") or p.get("usd_exchange_rate") or 1)
    photo = p.get("photo") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "blurb": p.get("blurb"),
        "url": (p.get("urls") or {}).get("web", {}).get("project"),
        "category": (p.get("category") or {}).get("name"),
        "creator": (p.get("creator") or {}).get("name"),
        "location": (p.get("location") or {}).get("displayable_name"),
        "country": p.get("country_displayable_name"),
        "currency": p.get("currency"),
        "currencySymbol": p.get("currency_symbol"),
        "goal": p.get("goal"),
        "pledged": p.get("pledged"),
        "usdPledged": p.get("usd_pledged"),
        "goalUsd": round(goal * rate, 2),
        "percentFunded": p.get("percent_funded"),
        "backers": p.get("backers_count"),
        "deadline": p.get("deadline"),
        "launchedAt": p.get("launched_at"),
        "staffPick": p.get("staff_pick"),
        "sources": p.get("_sources"),
        "kwHits": p.get("_kwHits"),
        "score": p.get("_score"),
        "coverRemote": photo.get("1024x576") or photo.get("full") or photo.get("med") or "",
        "videoMp4": p.get("videoMp4"),
        "videoHls": p.get("videoHls"),
        "rewards": p.get("rewards", []),
    }


# ---------------------------------------------------------------- 存储层
def load_snapshots():
    if not os.path.exists(DATA_JSON):
        return []
    try:
        data = json.load(open(DATA_JSON, encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        log("⚠ ks_data.json 解析失败，已备份重建: %s" % e)
        try:
            os.replace(DATA_JSON, DATA_JSON + ".broken")
        except Exception:
            pass
        return []


def save_snapshots(snaps):
    snaps = sorted(snaps, key=lambda d: d.get("date", ""), reverse=True)[:KEEP_DAYS]
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(snaps, f, ensure_ascii=False, indent=1)
    return snaps


def read_history():
    if not os.path.exists(HIST_CSV):
        return []
    with open(HIST_CSV, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_history(snap):
    """累计台账：按 (日期, 项目ID) 去重后追加，永不删除历史行"""
    fields = ["Date", "ProjectID", "Name", "Category",
              "USD_Pledged", "Backers", "Min_Price", "Tiers", "URL"]
    rows = []
    for p in snap["projects"]:
        prices = [r["price"] for r in p.get("rewards", []) if r.get("price")]
        rows.append({
            "Date": snap["date"],
            "ProjectID": p.get("id"),
            "Name": p.get("name"),
            "Category": p.get("category"),
            "USD_Pledged": round(float(p.get("usdPledged") or 0), 2),
            "Backers": p.get("backers"),
            "Min_Price": min(prices) if prices else "",
            "Tiers": len(p.get("rewards", [])),
            "URL": p.get("url"),
        })
    seen, old = set(), []
    if os.path.exists(HIST_CSV):
        with open(HIST_CSV, "r", encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            for r in rd:
                seen.add((r.get("Date"), str(r.get("ProjectID"))))
                old.append({k: r.get(k, "") for k in fields})
    added = 0
    for r in rows:
        key = (r["Date"], str(r["ProjectID"]))
        if key in seen:
            continue
        old.append(r)
        seen.add(key)
        added += 1
    with open(HIST_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(old)
    log("历史台账累计 %d 行（本次新增 %d 行）" % (len(old), added))
    return old


# ---------------------------------------------------------------- 页面渲染
NAV = """<div style='position:relative; margin-bottom:25px;'>
        <div style='text-align:center; font-size:1.2rem;'>
            <a href='index.html'>🏠 技术趋势</a> | <a href='news.html'>🌍 国际要闻</a> | <a href='finance.html'>📈 金融看板</a> | <a href='kickstarter.html'><b>💡 众筹热点</b></a>
        </div>
        <div style='position:absolute; top:0; right:0; font-size:0.9rem; color:#666;'>
            @ApexH | 📅 {date}
        </div>
    </div><hr>"""

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#16181d;--muted:#6b7280;--line:#e4e7ec;
--accent:#037362;--accent2:#05ce78;--warn:#b45309;--chip:#eef2ff;--chipline:#c7d2fe}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);
font-family:"Segoe UI",system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
header.top{background:linear-gradient(135deg,#037362,#05ce78);color:#fff;border-radius:14px;
padding:24px 26px 20px;margin:18px 0 0;box-shadow:0 6px 20px rgba(3,115,98,.18)}
header.top h1{margin:0 0 6px;font-size:25px;letter-spacing:.5px}
header.top .sub{opacity:.92;font-size:14px}
.selector{margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
select,button.btn{font:inherit;font-size:14px;padding:7px 12px;border-radius:8px;
border:1px solid rgba(255,255,255,.55);background:rgba(255,255,255,.16);color:#fff;cursor:pointer}
button.btn{background:#fff;color:#037362;border:none;font-weight:600}
select option{color:#16181d}
.meta{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;opacity:.9;margin-top:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
margin-bottom:22px;overflow:hidden;box-shadow:0 2px 10px rgba(16,24,40,.05)}
.hero{position:relative;width:100%;height:320px;background:#0e1116;overflow:hidden}
.hero img{width:100%;height:100%;object-fit:cover;display:block}
.hero video{width:100%;height:100%;object-fit:cover;display:block;background:#000}
.hero .badge{position:absolute;top:14px;left:14px;background:rgba(3,115,98,.94);color:#fff;
padding:5px 11px;border-radius:999px;font-size:12.5px;font-weight:600}
.hero .idx{position:absolute;top:14px;right:14px;background:rgba(0,0,0,.55);color:#fff;
width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font-weight:700;font-size:15px}
.body{padding:20px 22px 22px}
h2.name{margin:0 0 6px;font-size:21px;line-height:1.35}
.blurb{color:#374151;font-size:15px;margin:0 0 14px}
.tags{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.tag{background:var(--chip);border:1px solid var(--chipline);color:#3730a3;
font-size:12.5px;padding:3px 9px;border-radius:6px}
.tag.g{background:#ecfdf5;border-color:#a7f3d0;color:#065f46}
.tag.o{background:#fff7ed;border-color:#fed7aa;color:#9a3412}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}
.stat{background:#f9fafb;border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat .k{font-size:12px;color:var(--muted)}
.stat .v{font-size:18px;font-weight:700;margin-top:2px}
.bar{height:9px;background:#e5e7eb;border-radius:999px;overflow:hidden;margin-bottom:6px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#037362,#05ce78)}
.barlab{font-size:12.5px;color:var(--muted);margin-bottom:16px}
h3.sec{font-size:15px;margin:20px 0 10px;padding-left:9px;border-left:3px solid var(--accent2)}
table.rw{width:100%;border-collapse:collapse;font-size:14px}
table.rw th{text-align:left;background:#f9fafb;color:#374151;font-weight:600;font-size:13px;
padding:8px 10px;border-bottom:1px solid var(--line)}
table.rw td{padding:9px 10px;border-bottom:1px solid #f1f3f5;vertical-align:top}
table.rw tr:hover td{background:#fafbfc}
.price{font-weight:700;color:#037362;white-space:nowrap}
.rtitle{font-weight:600}
.rdesc{color:#4b5563;font-size:13px;margin-top:3px}
.so{color:var(--warn);font-weight:600}
a.cta{display:inline-block;margin-top:16px;background:#037362;color:#fff;text-decoration:none;
padding:9px 16px;border-radius:9px;font-weight:600;font-size:14px}
a.cta:hover{background:#025a4e}
details{margin-top:10px}
summary{cursor:pointer;color:#0369a1;font-size:13.5px;padding:4px 0}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;
margin-top:26px;padding:18px 20px 20px;box-shadow:0 2px 10px rgba(16,24,40,.05)}
.panel h2{margin:0 0 4px;font-size:19px}
.panel .hint{color:var(--muted);font-size:13px;margin-bottom:14px}
table.hist{width:100%;border-collapse:collapse;font-size:13.5px}
table.hist th{text-align:left;background:#f9fafb;color:#374151;font-weight:600;
padding:8px 10px;border-bottom:1px solid var(--line);position:sticky;top:0}
table.hist td{padding:7px 10px;border-bottom:1px solid #f1f3f5}
table.hist tr:hover td{background:#fafbfc}
.scroll{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:10px}
footer.note{margin-top:26px;color:var(--muted);font-size:12.5px;line-height:1.8;
border-top:1px solid var(--line);padding-top:14px}
.empty{padding:40px;text-align:center;color:var(--muted)}
@media(max-width:640px){.hero{height:210px}.panel,.card{border-radius:10px}}
"""

JS = """
const DATA = __DATA__;
const HIST = __HIST__;
const el = (id)=>document.getElementById(id);

function money(n){
  if(n===null||n===undefined||n==='') return '-';
  return '$' + Number(n).toLocaleString('en-US',{maximumFractionDigits:0});
}
function esc(s){
  return String(s===null||s===undefined?'':s).replace(/[&<>"']/g, c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function dateStr(ts){
  if(!ts) return '-';
  return new Date(ts*1000).toLocaleDateString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit'});
}
function timeLeft(deadline, ref){
  const d = Math.max(0, (deadline*1000 - Date.parse(ref)))/1000;
  const days = Math.floor(d/86400), hrs = Math.floor((d%86400)/3600);
  if(days>0) return days + ' 天 ' + hrs + ' 小时';
  return hrs + ' 小时';
}
function statusOf(r){
  if(r.remaining===0) return '<span class="so">售罄</span>';
  if(r.remaining!==null && r.remaining!==undefined) return '剩 '+r.remaining+' / '+r.limit+' <span class="so">限量</span>';
  if(r.limit) return '限量 '+r.limit;
  return '不限量';
}
function projectCard(p,i){
  const cover = p.coverRemote||'';
  const tags = [];
  if(p.category) tags.push('<span class="tag">'+esc(p.category)+'</span>');
  if(p.location) tags.push('<span class="tag">'+esc(p.location)+'</span>');
  if(p.staffPick) tags.push('<span class="tag g">Projects We Love</span>');
  (p.kwHits||[]).slice(0,5).forEach(k=>tags.push('<span class="tag o">'+esc(k)+'</span>'));

  const videoHtml = p.videoMp4
    ? '<video controls preload="none" poster="'+esc(cover)+'" src="'+esc(p.videoMp4)+'"></video>'
    : '<img loading="lazy" src="'+esc(cover)+'" alt="">';

  const rws = (p.rewards||[]);
  const head = rws.slice(0,8), rest = rws.slice(8);
  function rows(list){
    return list.map(r=>('<tr>'+
      '<td class="price">'+money(r.price)+'</td>'+
      '<td><div class="rtitle">'+esc(r.title||'标准档')+'</div>'+
        (r.desc?'<div class="rdesc">'+esc(r.desc).slice(0,190)+'</div>':'')+'</td>'+
      '<td>'+(r.backers!==null&&r.backers!==undefined?esc(r.backers):'-')+'</td>'+
      '<td>'+statusOf(r)+'</td>'+
      '<td>'+dateStr(r.eta)+'</td>'+
      '</tr>')).join('');
  }
  let tableHtml;
  if(rws.length){
    tableHtml = '<table class="rw"><thead><tr><th>价格</th><th>包含内容</th><th>支持人数</th><th>配额</th><th>预计发货</th></tr></thead><tbody>'
      + rows(head) + '</tbody></table>';
    if(rest.length){
      tableHtml += '<details><summary>展开其余 '+rest.length+' 个档次</summary><table class="rw"><tbody>'
        + rows(rest) + '</tbody></table></details>';
    }
  } else {
    tableHtml = '<p style="color:#6b7280;font-size:14px">该项目未公开档次明细，请前往 Kickstarter 查看。</p>';
  }

  return '<article class="card">'+
    '<div class="hero"><div class="badge">'+esc(p.category||'')+'</div><div class="idx">'+(i+1)+'</div>'
      + videoHtml + '</div>'+
    '<div class="body">'+
      '<h2 class="name">'+esc(p.name)+'</h2>'+
      '<p class="blurb">'+esc(p.blurb)+'</p>'+
      '<div class="tags">'+tags.join('')+'</div>'+
      '<div class="stats">'+
        '<div class="stat"><div class="k">已筹金额 (USD)</div><div class="v">'+money(p.usdPledged)+'</div></div>'+
        '<div class="stat"><div class="k">目标金额</div><div class="v">'+money(p.goalUsd)+'</div></div>'+
        '<div class="stat"><div class="k">达成率</div><div class="v">'+(p.percentFunded?Number(p.percentFunded).toFixed(0)+'%':'-')+'</div></div>'+
        '<div class="stat"><div class="k">支持者</div><div class="v">'+(p.backers?Number(p.backers).toLocaleString('en-US'):'-')+'</div></div>'+
        '<div class="stat"><div class="k">剩余时间</div><div class="v">'+timeLeft(p.deadline, DATA_REF)+'</div></div>'+
      '</div>'+
      '<div class="bar"><i style="width:'+Math.min(100,Number(p.percentFunded||0))+'%"></i></div>'+
      '<div class="barlab">'+money(p.usdPledged)+' / 目标 '+money(p.goalUsd)+'　·　发起人 '+esc(p.creator||'-')+'</div>'+
      '<h3 class="sec">选购档次（共 '+rws.length+' 档）</h3>'+tableHtml+
      '<a class="cta" href="'+esc(p.url)+'" target="_blank" rel="noopener">前往 Kickstarter 项目页 →</a>'+
    '</div></article>';
}

function renderHist(){
  if(!HIST.length) return;
  const body = HIST.map(r=>('<tr>'+
    '<td>'+esc(r.d)+'</td>'+
    '<td><a href="'+esc(r.u)+'" target="_blank" rel="noopener">'+esc(r.n)+'</a></td>'+
    '<td>'+esc(r.c)+'</td>'+
    '<td>'+money(r.p)+'</td>'+
    '<td>'+(r.b?Number(r.b).toLocaleString('en-US'):'-')+'</td>'+
    '<td>'+money(r.m)+'</td>'+
    '</tr>')).join('');
  el('histwrap').innerHTML = '<div class="scroll"><table class="hist"><thead><tr>'+
    '<th>日期</th><th>项目</th><th>类目</th><th>已筹(USD)</th><th>支持者</th><th>最低档</th>'+
    '</tr></thead><tbody>'+body+'</tbody></table></div>';
}

function render(){
  const d = DATA.find(x=>x.date===current);
  const box = el('list');
  el('dsel').value = current;
  if(!d){ box.innerHTML='<div class="empty">这一天没有数据。</div>'; return; }
  el('gen').textContent = '更新于 ' + d.generatedAt.replace('T',' ').slice(0,19) + ' (UTC+7)';
  el('pool').textContent = d.poolSize + ' 个候选';
  box.innerHTML = d.projects.map(projectCard).join('');
  window.scrollTo({top:0,behavior:'smooth'});
}

let current;
function init(){
  const sel = el('dsel');
  DATA.forEach(d=>{
    const o=document.createElement('option');
    o.value=d.date; o.textContent=d.date+'（'+d.projects.length+' 个项目）';
    sel.appendChild(o);
  });
  current = DATA[0].date;
  sel.addEventListener('change', e=>{ current=e.target.value; render(); });
  el('prev').addEventListener('click', ()=>{
    const i=DATA.findIndex(x=>x.date===current);
    if(i<DATA.length-1){ current=DATA[i+1].date; render(); }
  });
  el('next').addEventListener('click', ()=>{
    const i=DATA.findIndex(x=>x.date===current);
    if(i>0){ current=DATA[i-1].date; render(); }
  });
  renderHist();
  render();
}
const DATA_REF = DATA[0].generatedAt;
document.addEventListener('DOMContentLoaded', init);
"""

HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>众筹热点 · 每日科技众筹 Top 5</title>
<style>__CSS__</style></head>
<body><div class="wrap">
__NAV__
__ALERT__
<header class="top">
  <h1>💡 Kickstarter 每日科技众筹 Top 5</h1>
  <div class="sub">智能硬件 · 软件 · AI · 自动化 —— 与财经/新闻看板共用同一定时更新引擎</div>
  <div class="selector">
    <select id="dsel"></select>
    <button class="btn" id="prev">← 前一天</button>
    <button class="btn" id="next">后一天 →</button>
  </div>
  <div class="meta">
    <span id="gen"></span><span id="pool"></span>
    <span>数据源：Kickstarter Discover API + 项目详情页</span>
  </div>
</header>
<div id="list"></div>
<section class="panel">
  <h2>📚 累计上榜台账</h2>
  <div class="hint">历史上榜项目全量留档（ks_history.csv），此处展示最近 __ARCHIVE_DAYS__ 天，共 __HIST_N__ 条。</div>
  <div id="histwrap"></div>
</section>
<footer class="note">
  排序口径：Kickstarter popularity 位次 + 已筹金额 + 支持人数 + AI/自动化关键词加权，取前 5。<br>
  金额已折算为美元（USD）；其他货币金额以 Kickstarter 页面为准。<br>
  封面图片与视频直连 Kickstarter CDN，版权归各项目发起方所有，本页面仅作每日选品参考。
</footer>
</div>
<script>__JS__</script>
</body></html>
"""


def render_page(snaps, hist_rows, today, stale=None):
    payload = json.dumps(snaps, ensure_ascii=False).replace("</script>", "<\\/script>")
    recent = [r for r in hist_rows if r.get("Date")]
    recent = sorted(recent, key=lambda r: r["Date"], reverse=True)[:ARCHIVE_DAYS * TOP_N]
    compact = [{"d": r["Date"], "n": r["Name"], "c": r["Category"],
                "p": r["USD_Pledged"], "b": r["Backers"], "m": r["Min_Price"],
                "u": r["URL"]} for r in recent]
    hist_js = json.dumps(compact, ensure_ascii=False).replace("</script>", "<\\/script>")

    if stale:
        newest = snaps[0]["date"] if snaps else "-"
        alert = ("<div class='alert'>⚠ <b>今日数据抓取失败</b>（%s）。当前页面显示的"
                 "仍是 <b>%s</b> 的榜单，非最新。常见原因是 Cloudflare 拒绝了数据中"
                 "心出口 IP，通常下一次运行会自行恢复。</div>" % (stale, newest))
    else:
        alert = ""

    js = JS.replace("__DATA__", payload).replace("__HIST__", hist_js)
    page = (HTML_TPL
            .replace("__CSS__", CSS)
            .replace("__JS__", js)
            .replace("__ALERT__", alert)
            .replace("__NAV__", NAV.format(date=today))
            .replace("__ARCHIVE_DAYS__", str(ARCHIVE_DAYS))
            .replace("__HIST_N__", str(len(compact))))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    log("页面已生成 kickstarter.html（%.0f KB）" % (os.path.getsize(OUT_HTML) / 1024))


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只抓取，不写文件")
    args = ap.parse_args()

    log("curl 可执行档: %s" % CURL)
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    log("开始抓取 %s 的 Kickstarter 数据" % today)

    cands = collect_candidates()
    if not cands:
        log("⚠ 本次未抓到任何候选项目，保留原有页面不覆盖")
        return 1
    log("候选合计 %d 个，开始打分" % len(cands))
    cands.sort(key=score, reverse=True)
    top = cands[:TOP_N]

    for p in top:
        log("选中 [%s] %s  ($%s / %s人 / %s档)" % (
            p["_score"], (p.get("name") or "")[:44],
            format(float(p.get("usd_pledged") or 0), ",.0f"),
            p.get("backers_count"), len(p.get("rewards", []))))
        enrich(p)

    snap = {
        "date": today,
        "generatedAt": now.isoformat(timespec="seconds"),
        "source": "Kickstarter discover/advanced API + 项目详情页",
        "method": "popularity 位次 + 筹款/支持人数加权 + AI/自动化关键词加权",
        "poolSize": len(cands),
        "projects": [simplify(p) for p in top],
    }

    if args.dry:
        for p in snap["projects"]:
            print(" * %-52s 档次%3d  封面%5s  视频%5s" % (
                (p["name"] or "")[:52], len(p["rewards"]),
                bool(p["coverRemote"]), bool(p["videoMp4"])))
        return 0

    snaps = [s for s in load_snapshots() if s.get("date") != today]
    snaps.insert(0, snap)
    snaps = save_snapshots(snaps)
    log("快照库保留 %d 天（上限 %d 天）" % (len(snaps), KEEP_DAYS))

    hist_rows = append_history(snap)
    render_page(snaps, hist_rows, today)
    log("众筹看板更新完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
