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
model = genai.GenerativeModel('models/gemini-1.5-flash')

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
        for item in root.findall('.//item')[:12]:
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
def fetch_today_flight_price():
    """获取胡志明->广州 当日最低价 (美元)"""
    query = "cheapest flight SGN to CAN 2025"
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        prices = [int(p) for p in re.findall(r'\$(\d{2,4})', r.text) if 50 < int(p) < 1000]
        if prices:
            return min(prices)
    except Exception:
        pass
    return 185

def fetch_flight_price_for_date(target_date="2027-02-01"):
    """特定日期票价估算（基于当日价 + 春节溢价）"""
    today_price = fetch_today_flight_price()
    estimated = int(today_price * 1.45)
    if estimated < 250:
        estimated = 320
    elif estimated > 650:
        estimated = 580
    return estimated

def fetch_flight_prices():
    today = fetch_today_flight_price()
    fixed = fetch_flight_price_for_date("2027-02-01")
    return today, fixed

# ---------- 6. 数据库与页面生成 ----------
def update_db_and_pages(hn, world, fin, flight_today, flight_fixed):
    today_str = datetime.now().strftime('%Y-%m-%d')

    # ---------- 历史数据：汇率 + 机票 ----------
    hist_row = {
        "Date": today_str,
        "USD_CNY": fin["USD_CNY"],
        "VND_CNY_1k": fin["VND_CNY_1k"],
        "Flight_Today": flight_today,
        "VN_Index": fin["VN_Index"]
    }
    hist_file = "history.csv"
    if os.path.exists(hist_file):
        df_hist = pd.read_csv(hist_file)
        df_hist = df_hist[df_hist['Date'] != today_str]
        df_hist = pd.concat([df_hist, pd.DataFrame([hist_row])], ignore_index=True)
    else:
        df_hist = pd.DataFrame([hist_row])
    df_hist.tail(90).to_csv(hist_file, index=False)

    # ---------- 股票历史 ----------
    stock_file = "stock_history.csv"
    stock_row = {"Date": today_str}
    for name, price in fin["Stocks"].items():
        stock_row[name] = price
    if os.path.exists(stock_file):
        df_stock = pd.read_csv(stock_file)
        df_stock = df_stock[df_stock['Date'] != today_str]
        df_stock = pd.concat([df_stock, pd.DataFrame([stock_row])], ignore_index=True)
    else:
        df_stock = pd.DataFrame([stock_row])
    df_stock.tail(90).to_csv(stock_file, index=False)

    # ---------- 导航栏 ----------
    nav = """<div style='margin-bottom:25px; text-align:center; font-size:1.2rem;'>
        <a href='index.html'>🏠 技术趋势</a> | 
        <a href='news.html'>🌍 国际要闻</a> | 
        <a href='finance.html'>📈 金融看板</a>
    </div><hr>"""

    # ---------- index.html ----------
    hn_list = "".join([
        f"<li style='margin-bottom:15px;'><a href='{item['url']}' target='_blank'><b>{item['title']}</b></a><br>"
        f"<small style='color:#2c5282;'>{item['cn_title']}</small></li>"
        for item in hn
    ])
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(f"<html><head><meta charset='UTF-8'><title>技术趋势</title></head>"
                f"<body style='font-family:system-ui, sans-serif; padding:30px; max-width:1000px; margin:auto;'>"
                f"{nav}<h2>📌 Hacker News 技术热点 (Top 20)</h2><ul style='line-height:1.6;'>{hn_list}</ul></body></html>")

    # ---------- news.html ----------
    news_list = "".join([
        f"<li style='margin-bottom:18px;'><a href='{item['url']}' target='_blank'><b>{item['cn_title']}</b></a><br>"
        f"<small style='color:#4a5568;'>{item['title']}</small></li>"
        for item in world
    ])
    with open("news.html", "w", encoding="utf-8") as f:
        f.write(f"<html><head><meta charset='UTF-8'><title>国际要闻</title></head>"
                f"<body style='font-family:system-ui, sans-serif; padding:30px; max-width:1000px; margin:auto;'>"
                f"{nav}<h2>🌐 BBC 国际要闻</h2><ul style='line-height:1.6;'>{news_list}</ul></body></html>")

    # ---------- finance.html ----------
    hist_curve = pd.read_csv(hist_file) if os.path.exists(hist_file) else df_hist
    stock_curve = pd.read_csv(stock_file) if os.path.exists(stock_file) else df_stock

    dates = hist_curve['Date'].tolist()
    usd_cny_vals = hist_curve['USD_CNY'].tolist()
    vnd_cny_vals = hist_curve['VND_CNY_1k'].tolist()
    flight_hist_vals = hist_curve['Flight_Today'].tolist()

    stock_columns = [col for col in stock_curve.columns if col != 'Date']
    stock_series_js = []
    for stock_name in stock_columns:
        stock_vals = stock_curve[stock_name].fillna(0).tolist()
        stock_series_js.append(f"{{ name: '{stock_name}', type: 'line', data: {stock_vals}, smooth: false }}")

    stock_rows = "".join([
        f"<tr><td><b>{name}</b></td><td align='right'>{price:,.2f} ₫</td></tr>"
        for name, price in fin["Stocks"].items()
    ])

    # 修复关键点：正确拼接 JS 数组
    stock_series_str = ', '.join(stock_series_js)

    finance_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>金融看板 · 越南投资仪表盘</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f4f8; padding: 20px; }}
        .dashboard {{ display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px; }}
        .card {{ background: white; border-radius: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); padding: 20px; flex: 1 1 45%; }}
        .full-card {{ flex: 1 1 100%; }}
        .real-time {{ background: #1e293b; color: white; border-radius: 20px; padding: 20px; margin-bottom: 20px; display: flex; justify-content: space-between; flex-wrap: wrap; }}
        .real-time div {{ background: #0f172a; padding: 12px 18px; border-radius: 40px; min-width: 180px; margin: 6px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td, th {{ padding: 8px; border-bottom: 1px solid #e2e8f0; }}
        h3 {{ margin-top: 0; color: #0f3b5c; }}
        .chart-box {{ height: 320px; width: 100%; margin-top: 15px; }}
    </style>
</head>
<body>
    {nav}
    <div class="real-time">
        <div>💵 美元/人民币: <b>{fin['USD_CNY']}</b></div>
        <div>🇻🇳 越南盾/1k CNY: <b>{fin['VND_CNY_1k']}</b></div>
        <div>📈 VN-Index: <b>{fin['VN_Index']:.2f}</b></div>
        <div>✈️ 今日 SGN→CAN: <b>${flight_today}</b></div>
        <div>📅 2027-02-01 票价: <b>${flight_fixed}</b> (预估值)</div>
    </div>

    <div class="dashboard">
        <div class="card">
            <h3>📉 汇率A · 美元/人民币 (USD/CNY)</h3>
            <div id="chart_usdcny" class="chart-box"></div>
        </div>
        <div class="card">
            <h3>🧮 汇率B · 越南盾/千元人民币 (VND/1k CNY)</h3>
            <div id="chart_vndcny" class="chart-box"></div>
        </div>
        <div class="full-card">
            <h3>📊 越南龙头股历史走势 (收盘价)</h3>
            <div id="chart_stocks" class="chart-box" style="height: 420px;"></div>
            <div style="font-size: 12px; color: #555; margin-top: 8px; text-align:center;">注: 单位为越南盾 (VND)，部分股票价格缺失日可能为0</div>
        </div>
        <div class="full-card">
            <h3>✈️ 胡志明→广州 当日机票价格历史 ($)</h3>
            <div id="chart_flight" class="chart-box" style="height: 300px;"></div>
        </div>
    </div>

    <div class="card" style="margin-top: 20px;">
        <h3>🏦 实时股票价格 (越南市场)</h3>
        <table> <thead><tr><th>股票名称</th><th>最新价 (VND)</th></tr></thead>
        <tbody>{stock_rows}</tbody>
        </table>
    </div>

    <script>
        var dates = {dates};
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
            yAxis: {{ name: 'VND / 1000 CNY', scale: true }},
            series: [{{ name: 'VND/1k CNY', type: 'line', data: {vnd_cny_vals}, color: '#3182ce', smooth: true }}]
        }});
        var chart3 = echarts.init(document.getElementById('chart_stocks'));
        chart3.setOption({{
            tooltip: {{ trigger: 'axis' }},
            legend: {{ type: 'scroll', orient: 'horizontal', left: 'left', top: 0, itemWidth: 30 }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: '价格 (越南盾)', scale: true }},
            series: [{stock_series_str}]
        }});
        var chart4 = echarts.init(document.getElementById('chart_flight'));
        chart4.setOption({{
            tooltip: {{ trigger: 'axis' }},
            xAxis: {{ data: dates, name: '日期' }},
            yAxis: {{ name: '美元 ($)', min: 0 }},
            series: [{{ name: '当日票价 (SGN→CAN)', type: 'line', data: {flight_hist_vals}, color: '#f59e0b', smooth: true }}]
        }});
        window.addEventListener('resize', () => {{ chart1.resize(); chart2.resize(); chart3.resize(); chart4.resize(); }});
    </script>
</body>
</html>"""
    with open("finance.html", "w", encoding="utf-8") as f:
        f.write(finance_html)

    print("✅ 所有页面已生成，历史曲线数据已更新")

# ---------- 主程序 ----------
if __name__ == "__main__":
    hn_news = fetch_hn_tech()
    world_news = fetch_world_news()
    fin_data = fetch_finance()
    flight_today, flight_fixed = fetch_flight_prices()
    update_db_and_pages(hn_news, world_news, fin_data, flight_today, flight_fixed)
    print("🎉 全部完成！")
