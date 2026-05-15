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

# ---------- 1. 配置区 ----------
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('models/gemini-2.5-flash')

def translate_text(text, is_tech=True):
    """通用的 Gemini 翻译函数，带容错机制"""
    if not text:
        return ""
    domain = "技术新闻" if is_tech else "国际新闻要闻"
    prompt = f"你是一个专业的{domain}翻译。请将以下标题翻译成地道的中文。只需返回翻译结果：\n\n{text}"
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ 翻译异常: {e}")
        return "翻译处理中..."

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
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            link = a_tag.get('href')
            if not link:
                continue
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
    """安全的股票/汇率获取，失败返回 0.0"""
    try:
        ticker = yf.Ticker(code)
        price = ticker.fast_info.get('last_price', None)
        if price and price > 0:
            return round(price, 4)
        hist = ticker.history(period="1d")
        if not hist.empty and hist['Close'].iloc[-1] > 0:
            return round(hist['Close'].iloc[-1], 4)
    except Exception:
        pass
    return 0.0

def get_exchange_rate_fallback():
    """备用汇率接口"""
    try:
        resp = requests.get("https://api.exchangerate.host/latest?base=USD", timeout=10)
        data = resp.json()
        cny = data['rates'].get('CNY')
        vnd = data['rates'].get('VND')
        if cny and vnd:
            return cny, vnd
    except Exception:
        pass
    return None, None

def fetch_finance():
    print("📊 正在获取金融数据 (汇率+越南股票)...")
    # 汇率
    usd_cny = get_safe_price("CNY=X")
    usd_vnd = get_safe_price("VND=X")
    if usd_cny == 0.0 or usd_vnd == 0.0:
        fallback_cny, fallback_vnd = get_exchange_rate_fallback()
        if fallback_cny and fallback_vnd:
            usd_cny = fallback_cny
            usd_vnd = fallback_vnd
            print("⚠️ 使用备用汇率接口获取数据")
    if usd_cny == 0.0:
        usd_cny = 7.25
        print("⚠️ 美元/人民币采用默认值 7.25")
    if usd_vnd == 0.0:
        usd_vnd = 25400.0
        print("⚠️ 美元/越南盾采用默认值 25400")
    vnd_cny_1k = (usd_cny / usd_vnd) * 1000

    # 越南股票
    stocks = {
        "FPT": "FPT.VN", "Vietcombank": "VCB.VN", "Vinamilk": "VNM.VN", "Hoa Phat": "HPG.VN",
        "Mobile World": "MWG.VN", "BIDV": "BID.VN", "Vinhomes": "VHM.VN", "Masan": "MSN.VN",
        "PV Gas": "GAS.VN", "SSI": "SSI.VN"
    }
    stock_data = {}
    for name, code in stocks.items():
        price = get_safe_price(code)
        if price == 0.0:
            # 重试一次
            time.sleep(1)
            price = get_safe_price(code)
        stock_data[name] = price

    # 越南指数（仅展示，不用于曲线）
    vn_index = get_safe_price("^VNINDEX")  # yfinance 正确代码
    if vn_index == 0.0:
        print("⚠️ 无法获取 VN-Index，将显示为 0")

    return {
        "USD_CNY": round(usd_cny, 4),
        "VND_CNY_1k": round(vnd_cny_1k, 4),
        "Stocks": stock_data,
        "VN_Index": vn_index
    }

# ---------- 5. 机票价格监控 (当日 + 固定日期 2027-02-01) ----------

def fetch_flight_data_v2(target_date, is_fixed=False):
    """通过 SerpApi 抓取南航直达机票数据"""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return 0, "<tr><td colspan='3'>未配置 API Key</td></tr>"
    
    from serpapi import GoogleSearch
    params = {
        "engine": "google_flights",
        "departure_id": "SGN",
        "arrival_id": "CAN",
        "outbound_date": target_date,
        "currency": "CNY",  # <--- 改为人民币计价
        "hl": "zh-cn",
        "api_key": api_key,
        "type": "1",
        "stops": "1"
    }
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        flights = results.get("best_flights", []) + results.get("other_flights", [])
        cz_flights = []
        for f in flights:
            for seg in f.get("flights", []):
                name = seg.get("airline", "")
                if "China Southern" in name or "南方航空" in name:
                    cz_flights.append({
                        "flight_number": seg.get("flight_number", "CZ???"),
                        "price": f.get("price", 9999),
                        "departure": seg.get("departure_airport", {}).get("time", "未知")
                    })
        if not cz_flights:
            return 0, "<tr><td colspan='3'>未找到南航直达航班</td></tr>"
        
        cz_flights.sort(key=lambda x: x['price'])
        lowest_price = cz_flights[0]['price']
        rows_html = "".join([f"<tr><td style='padding:8px;border-bottom:1px solid #eee;'>{f['flight_number']}</td><td style='padding:8px;border-bottom:1px solid #eee;'>￥{f['price']}</td><td style='padding:8px;border-bottom:1px solid #eee;'>{f['departure']}</td></tr>" for f in cz_flights[:5]])
        return lowest_price, rows_html
    except Exception as e:
        print(f"Error fetching flight: {e}")
        return 0, f"<tr><td colspan='3'>错误: {str(e)}</td></tr>"

def fetch_today_flight_price():
    # 趋势参考：抓取 14 天后的机票
    target = (pd.Timestamp.now() + pd.Timedelta(days=14)).strftime('%Y-%m-%d')
    return fetch_flight_data_v2(target)

def fetch_fixed_date_flight():
    # 固定盯住 2027-02-01
    return fetch_flight_data_v2("2027-02-01", is_fixed=True)
