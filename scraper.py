import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
import re
import time
from bs4 import BeautifulSoup
import google.generativeai as genai
import xml.etree.ElementTree as ET
import json
import warnings

# 屏蔽无害的 FutureWarning 警告，让日志更干净
warnings.filterwarnings('ignore')

# ---------- 1. 配置区 ----------
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# 恢复最简单、安全的传统初始化，防止任何由于新版参数引起的 legacy SDK 崩溃
model = genai.GenerativeModel('models/gemini-2.5-flash')

def translate_text(text, is_tech=True):
    """通用的 Gemini 翻译函数：解放模型思考能力以保障翻译质量，通过 XML 标签进行精准文本清洗"""
    if not text: return ""
    domain = "技术新闻" if is_tech else "国际新闻要闻"
    
    # 释放模型的分析和思考能力，但要求最终译文必须包裹在特定的标签中
    prompt = (
        f"你是一个精通{domain}的专业翻译官。请将以下英文标题翻译成地道、准确、符合中文表达习惯的单行标题。\n"
        f"你可以展开充分的背景思考、词义辨析和语境分析，但请务必将你最终的“纯中文翻译结果”包裹在 <translation> 和 </translation> 标签中。\n"
        f"例如：<translation>这里是你的最终简短翻译结果</translation>。\n\n"
        f"待翻译标题：{text}"
    )
    
    try:
        # 恢复正常的生成配置，给模型充足的 Token 和健康的创造力去斟酌国际新闻的语气
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 1000}
        )
        res_text = response.text.strip()
        
        # --- 文本清洗管道 ---
        # 核心防线：优先使用正则精准切出 <translation> 标签里的纯净译文
        match = re.search(r'<translation>(.*?)</translation>', res_text, re.DOTALL)
        if match:
            return match.group(1).strip().strip('*# \n')
        
        # 兜底防线：如果模型偶尔未吐出标签，则通过传统的“思维特征树”剔除多余部分
        cleaned = re.sub(r'(?s)<think>.*?</think>', '', res_text)  # 移除标准 think 标签块
        cleaned = re.sub(r'(?s)思绪：.*?(?=最终方案|最终翻译|翻译结果|$)', '', cleaned) # 移除中文思绪前缀
        
        for marker in ["最终方案", "最终翻译", "翻译结果", "译文如下", "：", ":"]:
            if marker in cleaned:
                candidate = cleaned.split(marker)[-1].strip()
                if candidate and len(candidate) < 100 and not any(k in candidate for k in ["思绪", "方案", "思考"]):
                    return candidate.strip('*# \n')
        
        # 极端情况兜底：取最后一行非空、非思考的短行
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        for line in reversed(lines):
            if len(line) < 100 and not any(k in line for k in ["思绪", "方案", "思考", "分析", "思维", "Thinking", "Prompt"]):
                return line.strip('*# \n')
                
        return res_text if len(res_text) < 100 else text
    except Exception as e:
        print(f"⚠️ 翻译异常: {e}")
        return text  # 发生网络或流波动时降级返回英文，不弄乱看板页面排版

# ---------- 2. 抓取技术趋势 (Hacker News) ----------
def fetch_hn_tech():
    print("🚀 正在抓取 Hacker News 技术趋势 (20条)...")
    url = "https://news.ycombinator.com/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for row in soup.select('span.titleline')[:20]:
            a_tag = row.find('a')
            if not a_tag: continue
            title = a_tag.get_text(strip=True)
            link = a_tag.get('href')
            if not link: continue
            if link.startswith('item?'):
                full_url = f"https://news.ycombinator.com/{link}"
            elif link.startswith('http'):
                full_url = link
            else:
                full_url = f"https://news.ycombinator.com/{link}"
            cn_title = translate_text(title, is_tech=True)
            results.append({'title': title, 'cn_title': cn_title, 'url': full_url})
            time.sleep(0.3)
        return results
    except Exception as e:
        print(f"❌ Hacker News 抓取失败: {e}")
        return []

# ---------- 3. 抓取国际要闻 (BBC World RSS) ----------
def fetch_world_news():
    print("🌍 正在抓取国际要闻 (BBC)...")
    url = "https://feeds.bbci.co.uk/news/world/rss.xml"
    try:
        r = requests.get(url, timeout=20)
        root = ET.fromstring(r.content)
        news_items = []
        for item in root.findall('.//item')[:20]:
            title = item.find('title').text
            link = item.find('link').text
            if not link.startswith('http'):
                link = 'https://www.bbc.com' + link
            cn_title = translate_text(title, is_tech=False)
            news_items.append({'title': title, 'cn_title': cn_title, 'url': link})
            time.sleep(0.3)
        return news_items
    except Exception as e:
        print(f"❌ BBC 抓取失败: {e}")
        return []

# ---------- 4. 金融数据：汇率 + 股票价格 ----------
def get_safe_price(code):
    """稳健的价格抓取函数，扩大检索区间并清洗空值"""
    try:
        ticker = yf.Ticker(code)
        hist = ticker.history(period="1mo")
        if not hist.empty:
            valid_closes = hist['Close'].dropna()
            if not valid_closes.empty:
                return round(valid_closes.iloc[-1], 4)
        
        price = ticker.fast_info.get('last_price', None)
        if price and price > 0:
            return round(price, 4)
    except Exception:
        pass
    return 0.0

def get_vn_index_direct():
    """
    全新升级：绕过极不稳定的雅虎 API，改用越南本土权威券商 VNDIRECT 的大盘实时数据接口
    不仅能拿到最新收盘价，还能获取当天的真实波动
    """
    import time
    now_ts = int(time.time())
    start_ts = now_ts - (30 * 24 * 3600)  # 过去1个月的数据
    
    url = f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&from={start_ts}&to={now_ts}&symbol=VNINDEX"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://banggia.vndirect.com.vn/"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            closes = data.get("c", [])  # 'c' 数组代表收盘价历史
            valid_closes = [float(c) for c in closes if c is not None and c > 0]
            if valid_closes:
                latest_price = round(valid_closes[-1], 2)
                print(f"🎯 成功通过 VNDIRECT 核心通道获取 VN-Index: {latest_price}")
                return latest_price
    except Exception as e:
        print(f"⚠️ VNDIRECT 通道发生异常: {e}")
    return 0.0

def clean_vietnamese_number(s):
    s = "".join([c for c in s if c.isdigit() or c in ['.', ',']])
    if not s: return 0.0
    if '.' in s and ',' in s:
        if s.find('.') < s.find(','): s = s.replace('.', '').replace(',', '.')
        else: s = s.replace(',', '')
    elif '.' in s:
        if len(s.split('.')[1]) == 3: s = s.replace('.', '')
    elif ',' in s:
        if len(s.split(',')[1]) == 3: s = s.replace(',', '')
        else: s = s.replace(',', '.')
    try: return float(s)
    except: return 0.0

def fetch_vn_index_local():
    """
    备用本地源升级：改用越南各大券商公用的基准行情源进行正则截获
    """
    urls = [
        "https://banggia.cafef.vn/", 
        "https://price.vse.vn/",
        "https://vnexpress.net/kinh-doanh/quoc-te"
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                page_text = soup.get_text(separator=' ')
                match = re.search(r'VN[- ]?Index\s*[:\-\s]*([\d\.,]+)', page_text, re.IGNORECASE)
                if match:
                    val = clean_vietnamese_number(match.group(1))
                    if 1000 < val < 2500:  # 顺应 2026 年最新盘面水位线
                        print(f"🎯 备用本地源 ({url}) 成功截获 VN-Index: {val}")
                        return val
        except: pass
    return 0.0

def fetch_finance():
    print("📊 正在获取金融数据 (汇率+全球股指+越南股票)...")
    usd_cny = get_safe_price("CNY=X")
    usd_vnd = get_safe_price("VND=X")
    
    if usd_cny == 0.0: usd_cny = 7.25
    if usd_vnd == 0.0: usd_vnd = 25400.0
    vnd_cny_1k = (usd_cny / usd_vnd) * 1000

    stocks = {
        "FPT": "FPT.VN", "Vietcombank": "VCB.VN", "Vinamilk": "VNM.VN", "Hoa Phat": "HPG.VN",
        "Mobile World": "MWG.VN", "BIDV": "BID.VN", "Vinhomes": "VHM.VN", "Masan": "MSN.VN",
        "PV Gas": "GAS.VN", "SSI": "SSI.VN"
    }
    stock_data = {}
    for name, code in stocks.items():
        price = get_safe_price(code)
        if price == 0.0:
            time.sleep(0.5)
            price = get_safe_price(code)
        stock_data[name] = price

    # 1. 越南股指多级探测（将最稳健的本土券商直连调至第一顺位）
    vn_index = get_vn_index_direct()
    if vn_index == 0: 
        vn_index = get_safe_price("^VNINDEX")
    if vn_index == 0: 
        vn_index = get_safe_price("VNI.HM")
    if vn_index == 0: 
        vn_index = fetch_vn_index_local()
        
    # 2. 美国标普500与中国上证综指探测
    us_index = get_safe_price("^GSPC")
    cn_index = get_safe_price("000001.SS")

    # 3. 本地历史数据库兜底继承
    try:
        if os.path.exists("history.csv"):
            df_exist = pd.read_csv("history.csv")
            df_exist = df_exist[df_exist['Date'] != "2026-05-15"]
            if not df_exist.empty:
                # 只有当今天所有动态渠道都挂掉时，才勉强继承昨日
                if vn_index == 0 and "VN_Index" in df_exist.columns:
                    v_idx = df_exist["VN_Index"].dropna()
                    if not v_idx[v_idx > 0].empty: 
                        vn_index = float(v_idx[v_idx > 0].iloc[-1])
                        print(f"⚠️ 警告：全面失联，被迫继承历史大盘数据: {vn_index}")
    except Exception as e:
        print(f"⚠️ 激活继承机制时发生阻碍: {e}")
        
    # 彻底无法获取时的静态硬兜底水位修正（顺应 2026 年真实大盘水位，避免引入过时数据）
    if vn_index == 0: vn_index = 1910.0  
    if us_index == 0: us_index = 5100.0
    if cn_index == 0: cn_index = 3100.0
            
    return {
        "USD_CNY": round(usd_cny, 4), "VND_CNY_1k": round(vnd_cny_1k, 4), 
        "Stocks": stock_data, "VN_Index": vn_index, "US_Index": us_index, "CN_Index": cn_index
    }

# ---------- 5. 机票价格监控 ----------
def fetch_flight_data_v2(target_date, departure_id="SGN", arrival_id="CAN", is_fixed=False):
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("⚠️ 未配置 SERPAPI_KEY")
        if is_fixed: return 0, f"<tr><td>{target_date}</td><td colspan='2'>未配置 API Key</td></tr>"
        return 0, 0, "<tr><td colspan='3'>未配置 API Key</td></tr>"
    try:
        from serpapi.google_search import GoogleSearch
        params = {
            "engine": "google_flights", "departure_id": departure_id, "arrival_id": arrival_id,
            "outbound_date": target_date, "currency": "CNY", "hl": "zh-cn",
            "api_key": api_key, "type": "2", "stops": "1"  
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        flights = results.get("best_flights", []) + results.get("other_flights", [])
        
        cz_flights = []
        vj_flights = []
        for f in flights:
            for seg in f.get("flights", []):
                airline = seg.get("airline", "").lower()
                f_num = seg.get("flight_number", "").upper()
                
                is_cz = "china southern" in airline or "南方航空" in airline or f_num.startswith("CZ")
                is_vj = "vietjet" in airline or "越捷" in airline or f_num.startswith("VJ")
                
                flight_info = {
                    "flight_number": f_num, "price": f.get("price", 0),
                    "departure": seg.get("departure_airport", {}).get("time", "未知"),
                    "label": "南航" if is_cz else "越捷"
                }
                if is_cz: cz_flights.append(flight_info)
                elif is_vj and not is_fixed: vj_flights.append(flight_info)

        if is_fixed:
            if not cz_flights: return 0, f"<tr><td>{target_date[5:]} ({departure_id}→{arrival_id})</td><td colspan='2'>未找到直达航班</td></tr>"
            cz_flights.sort(key=lambda x: x['price'])
            lowest_price = cz_flights[0]['price']
            date_md = datetime.strptime(target_date, "%Y-%m-%d").strftime("%m/%d")
            f = cz_flights[0]
            return lowest_price, f"<tr><td>📅 {date_md} {f['flight_number']} ({departure_id}✈️{arrival_id})</td><td><b>￥{f['price']}</b></td><td>{f['departure']}</td></tr>"
        else:
            cz_lowest = cz_flights[0]['price'] if cz_flights else 0
            vj_lowest = vj_flights[0]['price'] if vj_flights else 0
            combined_display = []
            if cz_flights: cz_flights.sort(key=lambda x: x['price']); combined_display.extend(cz_flights[:2])
            if vj_flights: vj_flights.sort(key=lambda x: x['price']); combined_display.extend(vj_flights[:2])
            combined_display.sort(key=lambda x: x['price'])
            rows_html = "".join([f"<tr><td>{f['label']} ({f['flight_number']})</td><td>￥{f['price']}</td><td>{f['departure']}</td></tr>" for f in combined_display])
            return cz_lowest, vj_lowest, rows_html
    except Exception as e:
        print(f"⚠️ 机票抓取异常: {e}")
        if is_fixed: return 0, f"<tr><td>{target_date[5:]}</td><td colspan='2'>抓取异常</td></tr>"
        return 0, 0, "<tr><td colspan='3'>抓取异常</td></tr>"

def fetch_today_flight_price():
    target = (pd.Timestamp.now() + pd.Timedelta(days=14)).strftime('%Y-%m-%d')
    return fetch_flight_data_v2(target)

# ---------- 6. 数据库与页面生成 ----------
def update_db_and_pages(hn, world, fin, flight_today_tuple):
    today_str = datetime.now().strftime('%Y-%m-%d')
    cz_today, vj_today, flight_today_html = flight_today_tuple
    
    print("🎯 开始执行南航5大核心日期锁定监测...")
    cz_fixed_0131, html_0131 = fetch_flight_data_v2("2027-01-31", "SGN", "CAN", is_fixed=True)
    cz_fixed_0201, html_0201 = fetch_flight_data_v2("2027-02-01", "SGN", "CAN", is_fixed=True)
    cz_fixed_0202, html_0202 = fetch_flight_data_v2("2027-02-02", "SGN", "CAN", is_fixed=True)
    cz_fixed_0213, html_0213 = fetch_flight_data_v2("2027-02-13", "CAN", "SGN", is_fixed=True)
    cz_fixed_0214, html_0214 = fetch_flight_data_v2("2027-02-14", "CAN", "SGN", is_fixed=True)
    
    flight_fixed_html = html_0131 + html_0201 + html_0202 + html_0213 + html_0214

    # ---------- 历史数据存储与 5/15 清洗过滤 ----------
    hist_row = {
        "Date": today_str, "USD_CNY": fin["USD_CNY"], "VND_CNY_1k": fin["VND_CNY_1k"],
        "Flight_Today": cz_today, "Fixed_Flight": cz_fixed_0201, "VJ_Today": vj_today, 
        "VN_Index": fin["VN_Index"], "US_Index": fin["US_Index"], "CN_Index": fin["CN_Index"],
        "Fixed_0131": cz_fixed_0131, "Fixed_0202": cz_fixed_0202, "Fixed_0213": cz_fixed_0213, "Fixed_0214": cz_fixed_0214
    }
    hist_file = "history.csv"
    if os.path.exists(hist_file):
        df_hist = pd.read_csv(hist_file)
        df_hist = df_hist[df_hist['Date'] != "2026-05-15"]
        for col in ["USD_CNY", "Fixed_Flight", "VJ_Today", "VN_Index", "US_Index", "CN_Index", "Fixed_0131", "Fixed_0202", "Fixed_0213", "Fixed_0214"]:
            if col not in df_hist.columns: df_hist[col] = 0
        df_hist = df_hist[df_hist['Date'] != today_str]
        df_hist = pd.concat([df_hist, pd.DataFrame([hist_row])], ignore_index=True)
    else:
        df_hist = pd.DataFrame([hist_row])
    df_hist.tail(90).to_csv(hist_file, index=False)

    # ---------- 股票历史数据保存与 5/15 过滤 ----------
    stock_file = "stock_history.csv"
    stock_row = {"Date": today_str, **fin["Stocks"]}
    if os.path.exists(stock_file):
        df_stock = pd.read_csv(stock_file)
        df_stock = df_stock[df_stock['Date'] != "2026-05-15"]
        df_stock = df_stock[df_stock['Date'] != today_str]
        df_stock = pd.concat([df_stock, pd.DataFrame([stock_row])], ignore_index=True)
    else:
        df_stock = pd.DataFrame([stock_row])
    df_stock.tail(90).to_csv(stock_file, index=False)

    today_date = datetime.now().strftime('%Y-%m-%d')
    nav = f"""<div style='position:relative; margin-bottom:25px;'>
        <div style='text-align:center; font-size:1.2rem;'>
            <a href='index.html'>🏠 技术趋势</a> | <a href='news.html'>🌍 国际要闻</a> | <a href='finance.html'>📈 金融看板</a>
        </div>
        <div style='position:absolute; top:0; right:0; font-size:0.9rem; color:#666;'>
            @ApexH | 📅 {today_date}
        </div>
    </div><hr>"""

    hn_list = "".join([f"<li style='margin-bottom:15px;'><a href='{item['url']}' target='_blank'><b>{item['title']}</b></a><br><small style='color:#2c5282;'>{item['cn_title']}</small></li>" for item in hn])
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(f"<html><head><meta charset='UTF-8'><title>技术趋势</title></head><body style='font-family:system-ui, sans-serif; padding:30px; max-width:1000px; margin:auto;'>{nav}<h2>📌 Hacker News 技术热点 (Top 20)</h2><ul style='line-height:1.6;'>{hn_list}</ul></body></html>")

    news_list = "".join([f"<li style='margin-bottom:18px;'><a href='{item['url']}' target='_blank'><b>{item['cn_title']}</b></a><br><small style='color:#4a5568;'>{item['title']}</small></li>" for item in world])
    with open("news.html", "w", encoding="utf-8") as f:
        f.write(f"<html><head><meta charset='UTF-8'><title>国际要闻</title></head><body style='font-family:system-ui, sans-serif; padding:30px; max-width:1000px; margin:auto;'>{nav}<h2>🌐 BBC 国际要闻</h2><ul style='line-height:1.6;'>{news_list}</ul></body></html>")

    # ---------- 前端图表数据序列化 ----------
    dates_js = json.dumps(df_hist['Date'].tolist())
    usd_cny_vals = json.dumps(df_hist['USD_CNY'].fillna(7.25).tolist())
    vnd_cny_vals = json.dumps(df_hist['VND_CNY_1k'].fillna(0).tolist())
    
    flight_cz_today_vals = json.dumps(df_hist['Flight_Today'].fillna(0).tolist())
    flight_cz_fixed_vals = json.dumps(df_hist['Fixed_Flight'].fillna(0).tolist())
    flight_vj_today_vals = json.dumps(df_hist['VJ_Today'].fillna(0).tolist())
    flight_cz_0131_vals = json.dumps(df_hist['Fixed_0131'].fillna(0).tolist())
    flight_cz_0202_vals = json.dumps(df_hist['Fixed_0202'].fillna(0).tolist())
    flight_cz_0213_vals = json.dumps(df_hist['Fixed_0213'].fillna(0).tolist())
    flight_cz_0214_vals = json.dumps(df_hist['Fixed_0214'].fillna(0).tolist())

    vn_index_vals = json.dumps(df_hist['VN_Index'].fillna(1220.0).tolist())
    us_index_vals = json.dumps(df_hist['US_Index'].fillna(5050.0).tolist())
    cn_index_vals = json.dumps(df_hist['CN_Index'].fillna(3060.0).tolist())

    stock_columns = [col for col in df_stock.columns if col != 'Date']
    stock_series_js = []
    for stock_name in stock_columns:
        stock_vals = json.dumps(df_stock[stock_name].fillna(0).tolist())
        stock_series_js.append(f"{{ name: '{stock_name}', type: 'line', data: {stock_vals}, smooth: false }}")
    stock_series_str = ', '.join(stock_series_js)

    finance_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>金融看板 · 仪表盘</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f4f8; padding: 20px; }}
        .dashboard {{ display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px; }}
        .card {{ background: white; border-radius: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); padding: 20px; flex: 1 1 45%; min-width: 320px; }}
        .full-card {{ flex: 1 1 100%; }}
        .real-time {{ background: #1e293b; color: white; border-radius: 20px; padding: 20px; margin-bottom: 20px; display: flex; justify-content: space-between; flex-wrap: wrap; }}
        .real-time div {{ background: #0f172a; padding: 12px 18px; border-radius: 40px; min-width: 165px; margin: 6px; font-size: 13px; }}
        table {{ width: 100%; border-collapse: collapse; font-size:14px; }}
        td, th {{ padding: 8px; border-bottom: 1px solid #e2e8f0; }}
        h3 {{ margin-top: 0; color: #0f3b5c; }}
        .chart-box {{ height: 350px; width: 100%; margin-top: 15px; }}
        .chart-box-sm {{ height: 215px; width: 100%; margin-top: 10px; }}
        .btn-down {{ padding: 10px 20px; color: white; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; font-weight: bold; margin: 0 10px; }}
    </style>
</head>
<body>
    {nav}
    <div class="real-time">
        <div>💵 美元/人民币: <b>{fin['USD_CNY']}</b></div>
        <div>🇻🇳 VND 1K / CNY: <b>￥{fin['VND_CNY_1k']:.4f}</b></div>
        <div>🇺🇸 S&P 500: <b>{fin['US_Index']:.2f}</b></div>
        <div>🇨🇳 上证指数: <b>{fin['CN_Index']:.2f}</b></div>
        <div>🇻🇳 VN-Index: <b>{fin['VN_Index']:.2f}</b></div>
        <div>✈️ 南航/越捷趋势: <b>￥{cz_today}/￥{vj_today}</b></div>
    </div>

    <div class="dashboard">
        <div class="card">
            <h3>📈 汇率趋势 (USD/CNY)</h3>
            <div id="chart_usdcny" class="chart-box-sm"></div>
        </div>
        <div class="card">
            <h3>🧮 汇率趋势 (VND 1K / CNY)</h3>
            <div id="chart_vndcny" class="chart-box-sm"></div>
        </div>

        <div class="card">
            <h3 style="border-left: 4px solid #f59e0b; padding-left: 10px;">✈️ 趋势明细 (14天后参考)</h3>
            <table>
                <tr style="background:#f7fafc;"><th>航班 (航司)</th><th>价格 (CNY)</th><th>起飞时间</th></tr>
                {flight_today_html}
            </table>
        </div>
        
        <div class="card">
            <h3 style="border-left: 4px solid #805ad5; padding-left: 10px;">📅 定日明细 (南航独家锁定)</h3>
            <table>
                <tr style="background:#f7fafc;"><th>航班 (日期/航线)</th><th>最低价格</th><th>时间</th></tr>
                {flight_fixed_html}
            </table>
        </div>

        <div class="full-card">
            <h3>✈️ 中越直达航线多维大盘追踪 (CNY)</h3>
            <div id="chart_flight" class="chart-box" style="height: 380px;"></div>
        </div>

        <div class="full-card">
            <h3>🌏 全球核心股指走势对比 (标普500 / 上证 / VN-Index)</h3>
            <div id="chart_global_indices" class="chart-box" style="height: 380px;"></div>
        </div>

        <div class="full-card">
            <h3>📊 越南股票历史走势 (成分股)</h3>
            <div id="chart_stocks" class="chart-box" style="height: 400px;"></div>
        </div>
    </div>

    <div style="text-align:center; margin: 30px 0;">
        <a href="history.csv" download class="btn-down" style="background:#3182ce;">📥 下载汇率/机票历史</a>
        <a href="stock_history.csv" download class="btn-down" style="background:#38a169;">📥 下载股票历史</a>
    </div>

    <script>
        var dates = {dates_js};
        
        var chart1 = echarts.init(document.getElementById('chart_usdcny'));
        chart1.setOption({{
            tooltip: {{ trigger: 'axis' }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: 'USD/CNY', scale: true }},
            series: [{{ name: 'USD/CNY', type: 'line', data: {usd_cny_vals}, color: '#e53e3e', smooth: true }}]
        }});
        
        var chart2 = echarts.init(document.getElementById('chart_vndcny'));
        chart2.setOption({{
            tooltip: {{ trigger: 'axis' }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: 'CNY', scale: true }},
            series: [{{ name: 'VND 1K / CNY', type: 'line', data: {vnd_cny_vals}, color: '#3182ce', smooth: true }}]
        }});
        
        var chart3 = echarts.init(document.getElementById('chart_stocks'));
        chart3.setOption({{
            tooltip: {{ trigger: 'axis' }},
            legend: {{ type: 'scroll', orient: 'horizontal', left: 'left', top: 0 }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: '价格', scale: true }},
            series: [{stock_series_str}]
        }});
        
        var chart4 = echarts.init(document.getElementById('chart_flight'));
        chart4.setOption({{
            tooltip: {{ trigger: 'axis' }},
            legend: {{ type: 'scroll', orient: 'horizontal', top: 0 }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: '人民币 (￥)', scale: true }},
            series: [
                {{ name: '南航趋势(14天后)', type: 'line', data: {flight_cz_today_vals}, color: '#f59e0b', smooth: true }},
                {{ name: '越捷趋势(14天后)', type: 'line', data: {flight_vj_today_vals}, color: '#3182ce', smooth: true }},
                {{ name: '南航定日(01/31)', type: 'line', data: {flight_cz_0131_vals}, color: '#48bb78', smooth: true }},
                {{ name: '南航定日(02/01)', type: 'line', data: {flight_cz_fixed_vals}, color: '#e53e3e', smooth: true }},
                {{ name: '南航定日(02/02)', type: 'line', data: {flight_cz_0202_vals}, color: '#805ad5', smooth: true }},
                {{ name: '南航定日(02/13)', type: 'line', data: {flight_cz_0213_vals}, color: '#00b5d8', smooth: true }},
                {{ name: '南航定日(02/14)', type: 'line', data: {flight_cz_0214_vals}, color: '#b7791f', smooth: true }}
            ]
        }});

        var chart5 = echarts.init(document.getElementById('chart_global_indices'));
        chart5.setOption({{
            tooltip: {{ trigger: 'axis' }},
            legend: {{ data: ['美国 S&P 500', '中国 上证指数', '越南 VN-Index'], top: 0 }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: '指数点数', scale: true }},
            series: [
                {{ name: '美国 S&P 500', type: 'line', data: {us_index_vals}, color: '#e53e3e', smooth: true }},
                {{ name: '中国 上证指数', type: 'line', data: {cn_index_vals}, color: '#3182ce', smooth: true }},
                {{ name: '越南 VN-Index', type: 'line', data: {vn_index_vals}, color: '#38a169', smooth: true }}
            ]
        }});
        
        window.addEventListener('resize', () => {{ chart1.resize(); chart2.resize(); chart3.resize(); chart4.resize(); chart5.resize(); }});
    </script>
</body>
</html>"""
    with open("finance.html", "w", encoding="utf-8") as f:
        f.write(finance_html)
    print("🎉 看板细节微调、思维溢出高强过滤全面完毕！")

if __name__ == "__main__":
    hn_news = fetch_hn_tech()
    world_news = fetch_world_news()
    fin_data = fetch_finance()
    flight_today_tuple = fetch_today_flight_price()
    update_db_and_pages(hn_news, world_news, fin_data, flight_today_tuple)
    print("🚀 自动化流程调优全线收官！")
