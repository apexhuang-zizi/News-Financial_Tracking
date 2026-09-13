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
import tempfile
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
TOP_TIERS = 3          # 每个项目只展示支持人数最多的 N 个档次（档次表常达 30+ 项）
KEEP_DAYS = 7          # 完整快照保留天数（自动裁剪，防止仓库膨胀）
ARCHIVE_DAYS = 90      # 页面底部历史榜展示天数（数据源为累计 CSV）
TZ = timezone(timedelta(hours=7))

# ---- 中文字幕（本地 Whisper 转写 + 免费翻译），装机缺失时自动降级 ----
ASR_MODEL = os.environ.get("KS_ASR_MODEL", "base")   # tiny/base/small
ASR_MAX_SECONDS = 300  # 超过此长度的视频不做转写，避免耗时失控
MAX_CUE_CHARS = 42     # 单条字幕最大字符数，超过则按词拆分
GTX = "https://translate.googleapis.com/translate_a/single"
# Google 免费翻译接口常被限流(429)，必须有备用通道。MyMemory 免费、无需密钥、
# 且保留换行，作为 GTX 失败时的兜底。
MYMEM = "https://api.mymemory.translated.net/get"

# ---- 产品功能图文（详情页 story 由 JS 渲染，只能走 Jina 的 headless 渲染通道）----
STORY_MAX_IMG = 14
STORY_MAX_TEXT = 26
STORY_MAX_CHARS = 5000
# Jina 免费额度有限且会间歇性返回 CF 挑战页，主通道用耐心退避重试
JINA_TRIES = 2


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


def enrich(p, story_cache=None):
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
    total = len(clean)
    # 档次表动辄三四十项，全列出来没有信息量：只留支持人数最多的几个
    hot = sorted(clean, key=lambda x: (-(x.get("backers") or 0), x["price"]))[:TOP_TIERS]
    hot.sort(key=lambda x: x["price"])
    p["rewards"] = hot
    p["rewardsTotal"] = total

    video = p.get("video") or {}
    if isinstance(video, dict):
        p["videoMp4"] = video.get("high")
        p["videoHls"] = video.get("hls")
    for k in ("videoMp4", "videoHls"):
        v = p.get(k)
        if isinstance(v, str) and v.startswith("//"):
            p[k] = "https:" + v

    # 先做字幕（耗时长，顺便给后面的 Jina 请求留出间隔），再抓功能图文
    sub = build_subtitles(p, raw)
    if sub:
        p["cues"], p["vttLang"] = sub
    p["story"] = fetch_story(url, raw, p.get("blurb"),
                             story_cache.get(p["id"]) if story_cache else None)
    log("  ✓ %d/%d 档次 · 图文 %s · 字幕 %s" % (
        len(hot), total,
        ("%d 图/%d 段" % (sum(1 for b in (p["story"] or []) if b["t"] == "img"),
                       sum(1 for b in (p["story"] or []) if b["t"] == "p"))
         if p["story"] else "无"),
        ("%d 条" % len(p["cues"])) if p.get("cues") else "无"))


# ---------------------------------------------------------------- 中文字幕
def _pick_video_url(raw_html):
    """视频地址带时效签名，必须用详情页里最新的那个（列表接口里的往往已过期 403）"""
    s = htmllib.unescape(raw_html or "")
    best = {}
    for ts, sig, path in re.findall(
            r'https://v2\.kickstarter\.com/(\d{9,12})-([^"\\ ]{5,140}?)'
            r'/(projects/\d+/[^"\\ ]{5,90})', s):
        key = ("high" if "h264_high" in path else
               "base" if "h264_base" in path else "other")
        if key not in best or int(ts) > int(best[key][0]):
            best[key] = (ts, sig, path)
    for key in ("high", "base", "other"):
        if key in best:
            ts, sig, path = best[key]
            return "https://v2.kickstarter.com/%s-%s/%s" % (ts, sig, path)
    return None


def _translate_gtx(q):
    try:
        res = subprocess.run(
            [CURL, "-s", "-m", "25", "-G", GTX,
             "--data-urlencode", "client=gtx", "--data-urlencode", "sl=en",
             "--data-urlencode", "tl=zh-CN", "--data-urlencode", "dt=t",
             "--data-urlencode", "q=" + q],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace").stdout
        data = json.loads(res)
        return "".join(b[0] for b in data[0] if b and b[0])
    except Exception:
        return None


def _translate_mymem(q):
    try:
        res = subprocess.run(
            [CURL, "-s", "-m", "25", "-G", MYMEM,
             "--data-urlencode", "q=" + q,
             "--data-urlencode", "langpair=en|zh-CN"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace").stdout
        d = json.loads(res)
        t = ((d.get("responseData") or {}).get("translatedText") or "").strip()
        if t and "MYMEMORY WARNING" not in t:
            return t
    except Exception:
        pass
    return None


def translate_lines(lines, label=""):
    """英文行 -> 中文行。GTX 优先（可整批），失败则逐行走 MyMemory（单次有长度上限，
    必须逐行）。二者皆失败保留英文，保证功能不中断。"""
    if not lines:
        return list(lines)
    out = []
    for i in range(0, len(lines), 15):
        chunk = [" ".join((c or "").split()) for c in lines[i:i + 15]]
        q = "\n".join(chunk)
        txt = _translate_gtx(q)
        if txt:
            out.extend(t.strip() for t in txt.split("\n"))
        else:
            # GTX 不可用：逐行走 MyMemory，避免单次超长被拒
            for ln in chunk:
                t = _translate_mymem(ln)
                out.append(t.strip() if t else ln)
                time.sleep(0.15)
            if not any(chunk):
                pass
        if i + 15 < len(lines):
            time.sleep(0.4)
    if len(out) < len(lines):
        out += list(lines[len(out):])
    return out[:len(lines)]


def _split_segment(start, end, text, max_chars=MAX_CUE_CHARS):
    """Whisper 的片段可长达十几秒，按词切成适合阅读的短字幕并按字数分配时长"""
    text = " ".join((text or "").split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [(start, end, text)]
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    dur = max(0.1, end - start)
    total = sum(len(x) for x in lines) or 1
    out, t = [], start
    for ln in lines:
        d = dur * len(ln) / total
        out.append((t, t + d, ln))
        t += d
    return out


_ASR = None


def _asr_model():
    """延迟加载 Whisper：未安装时抛异常，由调用方降级"""
    global _ASR
    if _ASR is None:
        from faster_whisper import WhisperModel
        _ASR = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
    return _ASR


def build_subtitles(p, raw_html):
    """下载视频 -> 本地 Whisper 转写 -> 逐句译中 -> 返回 (cues, lang)。
    cues 为 [[start, end, text], ...]；任一步失败返回 None。"""
    url = _pick_video_url(raw_html) or p.get("videoMp4")
    if not url:
        return None
    try:
        _asr_model()
    except Exception:
        log("  · 未安装 faster-whisper，跳过中文字幕"
            "（pip install faster-whisper 后自动启用）")
        return None

    tmp = os.path.join(tempfile.gettempdir(), "ks_%s.mp4" % (p.get("id") or "tmp"))
    try:
        code = subprocess.run(
            [CURL, "-sSL", "-m", "180", "-A", UA,
             "-H", "Referer: https://www.kickstarter.com/",
             "-o", tmp, "-w", "%{http_code}", url],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace").stdout.strip()
        if code != "200" or not os.path.exists(tmp) or os.path.getsize(tmp) < 50000:
            log("  · 视频下载失败 HTTP %s，跳过字幕" % code)
            return None
        segs, info = _asr_model().transcribe(
            tmp, language="en", vad_filter=True, beam_size=1)
        if info.duration > ASR_MAX_SECONDS:
            log("  · 视频 %.0fs 过长，跳过字幕" % info.duration)
            return None
        cues = []
        for s in segs:
            cues.extend(_split_segment(s.start, s.end, s.text))
        if not cues:
            log("  · 视频无有效语音（%.0fs），跳过字幕" % info.duration)
            return None
        src = [c[2] for c in cues]
        zh = translate_lines(src, "字幕")
        lang = "zh" if any(a != b for a, b in zip(src, zh)) else "en"
        cues = [[round(a, 2), round(b, 2), t] for (a, b, _), t in zip(cues, zh)]
        log("  ✓ 字幕 %d 条（%s，视频 %.0fs / %.1fMB）"
            % (len(cues), "中译" if lang == "zh" else "英文",
               info.duration, os.path.getsize(tmp) / 1048576))
        return cues, lang
    except Exception as e:
        log("  · 字幕生成失败: %s" % str(e)[:80])
        return None
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# ---------------------------------------------------------------- 产品功能图文
def _jina(url, tries=JINA_TRIES):
    """r.jina.ai 免费额度有限且会间歇性返回 Cloudflare 挑战页，必须耐心退避重试。
    命中『Markdown Content:』即视为成功；否则按 15/35/55/75s 递增退避。"""
    res = ""
    for i in range(tries):
        try:
            res = subprocess.run([CURL, "-sSL", "-m", "120", "-A", UA, JINA + url],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stdout or ""
        except Exception:
            res = ""
        if "Markdown Content:" in res:
            return res
        if i < tries - 1:
            time.sleep(15 + i * 20)
    return res


def _parse_jina_story(md):
    """把 Jina 的 markdown 拆成 [{'t':'img','u'},{'t':'p','x'}] 块。"""
    body = md.split("Markdown Content:", 1)[1]
    blocks, seen, chars, n_img, n_txt = [], set(), 0, 0, 0
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith(("[", "#", ">", "|", "---")):
            continue
        m = re.match(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", ln)
        if m:
            u = m.group(1)
            if u in seen or n_img >= STORY_MAX_IMG:
                continue
            # 跳过头像/图标等极小配图（width/height 40~80 的多为头像）
            if re.search(r"[?&](?:width|height)=(?:40|60|80)\b", u):
                continue
            seen.add(u)
            n_img += 1
            blocks.append({"t": "img", "u": u})
            continue
        if len(ln) < 12 or n_txt >= STORY_MAX_TEXT or chars >= STORY_MAX_CHARS:
            continue
        n_txt += 1
        chars += len(ln)
        blocks.append({"t": "p", "x": ln})
    return blocks or None


def _translate_story(blocks):
    idx = [i for i, b in enumerate(blocks) if b["t"] == "p"]
    if idx:
        zh = translate_lines([blocks[i]["x"] for i in idx], "功能文案")
        for i, t in zip(idx, zh):
            blocks[i]["z"] = t
    return blocks


def _story_fallback(raw, blurb):
    """Jina 全失败时的兜底：用详情页里能直接拿到的产品图（带 sig 的 assets 大图）
    + 项目简介拼出一个『产品图集』，保证『产品功能』板块永不空白。"""
    blocks = []
    if raw:
        seen = set()
        clean = raw.replace("&amp;", "&")
        for u in re.findall(
                r"https://i\.kickstarter\.com/assets/[^\\\"\s]+?_original\."
                r"(?:jpg|png|webp)(?:\?[^\"\\\s]+)?", clean):
            base = u.split("?", 1)[0]
            if base in seen or len(blocks) >= STORY_MAX_IMG:
                continue
            if re.search(r"[?&](?:width|height)=(?:40|60|80)\b", u):
                continue
            seen.add(base)
            blocks.append({"t": "img", "u": u})
    if blurb:
        blocks.append({"t": "p", "x": (blurb or "").strip()})
    return _translate_story(blocks) if blocks else None


def fetch_story(url, raw=None, blurb=None, cached=None):
    """产品功能图文：story 由 JS 渲染，SSR 里没有，唯一稳定来源是 r.jina.ai 的
    headless 渲染。主通道 Jina（耐心退避重试）；全失败时【优先复用历史缓存】，
    其次回落到详情页已知产品图 + 简介，保证板块永不空白。
    返回 [{'t':'img','u':...}, {'t':'p','x':英文,'z':中文}] 或 None。"""
    md = _jina(url)
    if "Markdown Content:" in md:
        blocks = _parse_jina_story(md)
        if blocks:
            return _translate_story(blocks)
        log("  · Jina 正文无图文，改用兜底")
    else:
        log("  · Jina 全部通道失败")
    # 优先级：Jina 成功 > 历史缓存（限流时保板块不空白）> 详情页兜底图集
    if cached:
        return cached
    return _story_fallback(raw, blurb)


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
        "rewardsTotal": p.get("rewardsTotal") or len(p.get("rewards", [])),
        "cues": p.get("cues") or [],
        "vttLang": p.get("vttLang") or "",
        "story": p.get("story") or [],
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
            "Tiers": p.get("rewardsTotal") or len(p.get("rewards", [])),
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
font-size:17px;line-height:1.65;-webkit-font-smoothing:antialiased}
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

/* ---- 产品功能图文 ---- */
.story-bar{display:flex;gap:8px;align-items:center;margin:0 0 10px;flex-wrap:wrap}
.sbtn{font:inherit;font-size:13px;padding:5px 11px;border-radius:7px;
border:1px solid var(--line);background:#fff;color:#374151;cursor:pointer}
.sbtn:hover{background:#f9fafb}
.simgs{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 10px;
scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch}
.simgs img{flex:0 0 auto;width:270px;height:165px;object-fit:cover;border-radius:10px;
scroll-snap-align:start;background:#f3f4f6;border:1px solid var(--line)}
.stxt{margin-top:4px;max-height:270px;overflow:auto;
border-left:3px solid var(--accent2);padding:2px 0 2px 13px}
.stxt p{margin:0 0 11px;font-size:15.5px;line-height:1.8;color:#374151}
.stxt p .en{display:none;color:var(--muted);font-size:13.5px}
.stxt.en p .zh{display:none}
.stxt.en p .en{display:inline}

/* ---- 视频中文字幕（自绘浮层，规避跨域 <track> 限制）---- */
.cap{position:absolute;left:0;right:0;bottom:0;padding:10px 14px 14px;text-align:center;
color:#fff;font-size:18px;line-height:1.5;font-weight:600;z-index:3;pointer-events:none;
text-shadow:0 1px 3px rgba(0,0,0,.95),0 0 12px rgba(0,0,0,.8)}
.cap:empty{display:none}
.capbtn{position:absolute;top:14px;right:56px;z-index:4;background:rgba(0,0,0,.55);
color:#fff;border:none;border-radius:999px;padding:4px 11px;font-size:12.5px;
cursor:pointer;font-family:inherit}

@media(max-width:640px){
  body{font-size:16px}
  .hero{height:200px}
  .panel,.card{border-radius:10px}
  h2.name{font-size:19px}
  .blurb{font-size:15.5px}
  table.rw{font-size:14.5px}
  table.rw th,table.rw td{padding:8px 7px}
  .simgs img{width:215px;height:135px}
  .stxt p{font-size:15px}
  .cap{font-size:15.5px;padding:8px 10px 11px}
}
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
function storyHtml(p,i){
  const st = p.story||[];
  const imgs = st.filter(b=>b.t==='img'), paras = st.filter(b=>b.t==='p');
  if(!imgs.length && !paras.length) return '';
  const gal = imgs.length
    ? '<div class="simgs">'+imgs.map(b=>'<img loading="lazy" src="'+esc(b.u)+'" alt="">').join('')+'</div>'
    : '';
  const txt = paras.length
    ? '<div class="stxt" id="stxt'+i+'">'+paras.map(b=>
        '<p><span class="zh">'+esc(b.z||b.x)+'</span><span class="en">'+esc(b.x)+'</span></p>').join('')+'</div>'
    : '';
  const sw = (paras.length && paras.some(b=>b.z))
    ? '<div class="story-bar"><button class="sbtn" type="button" data-lang="'+i+'">显示英文原文</button></div>'
    : '';
  return '<h3 class="sec">产品功能 · 项目原帖图文</h3>'+sw+gal+txt;
}

function projectCard(p,i){
  const cover = p.coverRemote||'';
  const tags = [];
  if(p.category) tags.push('<span class="tag">'+esc(p.category)+'</span>');
  if(p.location) tags.push('<span class="tag">'+esc(p.location)+'</span>');
  if(p.staffPick) tags.push('<span class="tag g">Projects We Love</span>');
  (p.kwHits||[]).slice(0,5).forEach(k=>tags.push('<span class="tag o">'+esc(k)+'</span>'));

  const cues = p.cues||[];
  const videoHtml = p.videoMp4
    ? '<video controls preload="none" playsinline poster="'+esc(cover)+'" src="'+esc(p.videoMp4)+'" data-pi="'+i+'"></video>'
      + (cues.length
         ? '<div class="cap" id="cap'+i+'"></div>'
           + '<button class="capbtn" data-cap="'+i+'" type="button">字幕 '+(p.vttLang==='zh'?'中':'EN')+'</button>'
         : '')
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
      '<h3 class="sec">选购档次（支持人数最多的 '+rws.length+' 档 / 共 '+(p.rewardsTotal||rws.length)+' 档）</h3>'+tableHtml+
      storyHtml(p,i)+
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

/* 字幕自绘：跨域视频用原生 <track> 会被浏览器拦掉，改为监听 timeupdate 自己画 */
function wireCards(d){
  document.querySelectorAll('video[data-pi]').forEach(v=>{
    const i = +v.getAttribute('data-pi');
    const p = (d.projects||[])[i];
    const cues = (p && p.cues) || [];
    if(!cues.length) return;
    const box = document.getElementById('cap'+i);
    const btn = document.querySelector('button[data-cap="'+i+'"]');
    if(!box) return;
    let on = true;
    const paint = ()=>{
      if(!on){ box.textContent=''; return; }
      const t = v.currentTime;
      let lo=0, hi=cues.length-1, k=-1;
      while(lo<=hi){ const m=(lo+hi)>>1; if(cues[m][0]<=t){ k=m; lo=m+1; } else hi=m-1; }
      box.textContent = (k>=0 && t<=cues[k][1]) ? cues[k][2] : '';
    };
    v.addEventListener('timeupdate', paint);
    v.addEventListener('seeked', paint);
    if(btn) btn.addEventListener('click', ()=>{
      on = !on;
      btn.textContent = on ? ('字幕 '+(p.vttLang==='zh'?'中':'EN')) : '字幕 关';
      btn.style.opacity = on ? '1' : '.55';
      paint();
    });
  });
  document.querySelectorAll('button[data-lang]').forEach(b=>{
    b.addEventListener('click', ()=>{
      const box = document.getElementById('stxt'+b.getAttribute('data-lang'));
      if(!box) return;
      box.classList.toggle('en');
      b.textContent = box.classList.contains('en') ? '显示中文翻译' : '显示英文原文';
    });
  });
}

function render(){
  const d = DATA.find(x=>x.date===current);
  const box = el('list');
  el('dsel').value = current;
  if(!d){ box.innerHTML='<div class="empty">这一天没有数据。</div>'; return; }
  el('gen').textContent = '更新于 ' + d.generatedAt.replace('T',' ').slice(0,19) + ' (UTC+7)';
  el('pool').textContent = d.poolSize + ' 个候选';
  box.innerHTML = d.projects.map(projectCard).join('');
  wireCards(d);
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


KS_FILES = ["kickstarter.html", "ks_data.json", "ks_history.csv"]


def _git(*args):
    r = subprocess.run(["git"] + list(args), cwd=BASE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def git_push(snap, today, tiers):
    """把本次数据同步到远端。

    仓库现有的 GitHub Actions 每天会用 `git push --force` 覆盖 main，
    所以这里先 rebase 到远端最新，再重新合并台账（append_history 幂等），最后推送。
    """
    code, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch or "main"

    # 工作区必须先干净，否则 `git pull --rebase` 会直接失败（"You have unstaged
    # changes"），整个推送就废了、页面长期不更新。而 HTML 看板常被外部工具
    # （编辑器/预览面板）注入 data-page-node-id 之类的属性而变脏，所以这里把
    # 全部"生成产物"都还原，而不只是众筹三件套。源码文件不在此列，不会误伤。
    _git("checkout", "--", *KS_FILES)
    _git("checkout", "--", "index.html", "news.html", "finance.html",
         "history.csv", "stock_history.csv")

    code, out = _git("pull", "--rebase", "origin", branch)
    if code != 0:
        log("⚠ git pull 失败：%s" % out[:200])
        return 1

    # 远端可能在此期间追加过台账行，重新合并（按 Date+ProjectID 去重，幂等）
    hist_rows = append_history(snap)
    snaps = [s for s in load_snapshots() if s.get("date") != today]
    snaps.insert(0, snap)
    snaps = save_snapshots(snaps)
    render_page(snaps, hist_rows, today)

    _git("add", *KS_FILES)
    code, out = _git("commit", "-m", "Auto-update crowdfunding board: %s (%d tiers)"
                     % (today, tiers))
    if code != 0 and "nothing to commit" not in out:
        log("⚠ git commit 失败：%s" % out[:200])
        return 1
    code, out = _git("push", "origin", branch)
    if code != 0:
        log("⚠ git push 失败：%s" % out[:200])
        return 1
    log("已推送至远端 %s" % branch)
    return 0


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只抓取，不写文件")
    ap.add_argument("--push", action="store_true",
                    help="生成后自动 git pull --rebase / commit / push 到当前分支")
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

    # 预载历史快照，建立『项目ID -> 已抓产品图文』缓存：Jina 偶发限流时复用，
    # 保证『产品功能』板块不空白（Kickstarter 的 story 很少变动）。
    old = load_snapshots()
    story_cache = {}
    for _s in old:
        for _pr in _s.get("projects", []):
            _st = _pr.get("story")
            if _st and _pr.get("id") is not None:
                story_cache.setdefault(_pr["id"], _st)

    for p in top:
        log("选中 [%s] %s  ($%s / %s人 / %s档)" % (
            p["_score"], (p.get("name") or "")[:44],
            format(float(p.get("usd_pledged") or 0), ",.0f"),
            p.get("backers_count"), len(p.get("rewards", []))))
        enrich(p, story_cache)

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

    # 用档次总数（而非展示的 3 个）判定，避免裁剪逻辑干扰质量闸门
    old_tiers = sum(int(p.get("rewardsTotal") or len(p.get("rewards") or []))
                    for p in old[0]["projects"]) if old else 0
    new_tiers = sum(int(p.get("rewardsTotal") or 0) for p in snap["projects"])

    # 数据质量闸门：档次数据依赖详情页原始 HTML。若详情页全部失败（数据中心 IP
    # 被 Cloudflare 拒绝时必现），本次结果残缺，绝不能覆盖上一次的完整结果。
    if new_tiers == 0 and old_tiers > 0:
        msg = "详情页全部抓取失败，档次数据缺失，已保留上一次完整数据"
        log("⚠ " + msg)
        hist_rows = read_history()
        render_page(old, hist_rows, today, stale=msg)
        sys.stderr.write("::error::%s\n" % msg)
        return 1

    snaps = [s for s in old if s.get("date") != today]
    snaps.insert(0, snap)
    snaps = save_snapshots(snaps)
    log("快照库保留 %d 天（上限 %d 天），本次档次合计 %d 个"
        % (len(snaps), KEEP_DAYS, new_tiers))

    hist_rows = append_history(snap)
    render_page(snaps, hist_rows, today)
    log("众筹看板更新完成")

    if args.push:
        return git_push(snap, today, new_tiers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
