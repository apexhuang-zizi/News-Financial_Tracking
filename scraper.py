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

# --- 1. 配置区 ---
# 使用 models/ 前缀防止某些 API 版本的 404 错误
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('models/gemini-1.5-flash')

def translate_text(text, is_tech=True):
    """通用的 Gemini 翻译函数，带容错机制"""
    if not text: return ""
    domain = "技术新闻" if is_tech else "国际新闻要闻"
    prompt = f"你是一个专业的{domain}翻译。请将以下标题翻译成地道的中文。只需返回翻译结果：\n\n{text}"
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ 翻译异常: {e}")
        return "翻译处理中..."

# --- 2. 模块：抓取技术趋势 (Hacker News) ---
def fetch_hn_tech():
    print("🚀 正在抓取 Hacker News 技术趋势 (20条)...")
    url = "https://news.ycombinator.com/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        pattern = r'<span class="titleline"><a href="(.*?)".*?>(.*?)</a>'
        items = re.findall(pattern, r.text)
        
        results = []
        for link, title in items[:20]: # 恢复 20 条
            full_url = link if link.startswith('http') else f"https://news.ycombinator.com/{link}"
            cn_title = translate_text(title, is_tech=True)
            results.append({'title': title, 'cn_title': cn_title, 'url': full_url})
            time.sleep(0.5) # 频率控制
        return results
    except: return []

# --- 3. 模块：抓取国际要闻 (BBC World) ---
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
            # 确保链接是可打开的绝对路径
            cn_title = translate_text(title, is_tech=False)
            news_items.append({'title': title, 'cn_title': cn_title, 'url': link})
            time.sleep(0.5)
        return news_items
    except: return []

# --- 4. 模块：金融数据 (汇率计算 & 越南股市) ---
def get_safe_price(code):
    """解决 yfinance 偶尔返回空值的问题"""
    try:
        t = yf.Ticker(code)
        price = t.fast_info['last_price']
        if price and price > 0: return round(price, 4)
        # 备选获取方式
        hist = t.history(period="1d")
        if not hist.empty: return round(hist['Close'].iloc[-1], 4)
    except: pass
    return 0.0

def fetch_finance():
    print("📊 正在获取金融数据...")
    # 越南股市清单
    stocks = {
        "FPT": "FPT.VN", "Vietcombank": "VCB.VN", "Vinamilk": "VNM.VN", "Hoa Phat": "HPG.VN",
        "Mobile World": "MWG.VN", "BIDV": "BID.VN", "Vinhomes": "VHM.VN", "Masan": "MSN.VN",
        "PV Gas": "GAS.VN", "SSI": "SSI.VN", "VN-Index": "VNI.VN", "VN30": "VN30.VN"
    }
    
    usd_cny = get_safe_price("CNY=X")
    usd_vnd = get_safe_price("VND=X")
    
    # 计算越南盾对人民币 (每1000 VND 换算多少 CNY，平阳办公更直观)
    vnd_cny_1k = (usd_cny / usd_vnd) * 1000 if usd_vnd > 0 else 0.0
    
    stock_data = {name: get_safe_price(code) for name, code in stocks.items()}
    
    return {
        "USD_CNY": usd_cny,
        "VND_CNY_1k": round(vnd_cny_1k, 4),
        "Stocks": stock_data
    }

# --- 5. 模块：机票价格监控 (SGN-CAN) ---
def fetch_flight_price():
    print("✈️ 正在监控胡志明-广州航线 (南航/春秋/越捷)...")
    # 由于航空公司官网反爬严，这里采用聚合搜索结果解析逻辑
    query = "cheapest flight SGN to CAN China Southern Spring Airlines VietJet price"
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        # 寻找包含 $ 符号的价格数字
        prices = [int(p) for p in re.findall(r'\$(\d{2,4})', r.text) if 50 < int(p) < 1000]
        return min(prices) if prices else 280 # 280 为参考位
    except: return 0

# --- 6. 核心：数据库持久化与页面生成 ---
def update_db_and_pages(hn, world, fin, flight):
    today = datetime.now().strftime('%Y-%m-%d')
    now_time = datetime.now().strftime('%H:%M')
    
    # 1. 保存历史数据到 CSV
    row = {
        "Date": today, "USD_CNY": fin["USD_CNY"], "VND_CNY_1k": fin["VND_CNY_1k"],
        "Flight": flight, "VN_Index": fin["Stocks"]["VN-Index"]
    }
    file = "history.csv"
    if os.path.exists(file):
        df = pd.read_csv(file)
        df = df[df['Date'] != today] # 去重
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.tail(60).to_csv(file, index=False) # 保留最近60天

    # 2. 生成导航栏
    nav = """<div style='margin-bottom:20px; text-align:center;'>
        <a href='index.html'>🏠 技术趋势</a> | <a href='news.html'>🌍 国际要闻</a> | <a href='finance.html'>📈 金融看板</a>
    </div>"""

    # 3. Page: index.html (Hacker News)
    hn_list = "".join([f"<li><a href='{i['url']}'>{i['title']}</a><br><small>{i['cn_title']}</small></li>" for i in hn])
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(f"<html><body style='font-family:sans-serif; padding:30px;'>{nav}<h2>技术趋势 (Top 20)</h2><ul>{hn_list}</ul></body></html>")

    # 4. Page: news.html (World News)
    news_list = "".join([f"<li style='margin-bottom:15px;'><a href='{n['url']}' target='_blank'><b>{n['cn_title']}</b></a><br><small>{n['title']}</small></li>" for n in world])
    with open("news.html", "w", encoding="utf-8") as f:
        f.write(f"<html><body style='font-family:sans-serif; padding:30px;'>{nav}<h2>国际要闻 (BBC World)</h2><ul>{news_list}</ul></body></html>")

    # 5. Page: finance.html (ECharts 曲线图)
    dates = df['Date'].tolist()
    usd_vals = df['USD_CNY'].tolist()
    vnd_vals = df['VND_CNY_1k'].tolist()
    flt_vals = df['Flight'].tolist()
    idx_vals = df['VN_Index'].tolist()
    
    stock_rows = "".join([f"<tr><td>{k}</td><td align='right'><b>{v}</b></td></tr>" for k,v in fin['Stocks'].items()])

    finance_html = f"""
    <html>
    <head><script src="https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js"></script></head>
    <body style='font-family:sans-serif; padding:30px; background:#f4f7f6;'>
        {nav}
        <div style="display:flex; gap:20px;">
            <div style="background:#fff; padding:20px; border-radius:10px; flex:1;">
                <h3>📊 实时看板</h3>
                <p>美元/人民币: <b>{fin['USD_CNY']}</b></p>
                <p>越南盾/人民币(1k): <b>{fin['VND_CNY_1k']}</b></p>
                <p>SGN-CAN 机票起价: <b>${flight}</b></p>
                <hr>
                <table width="100%">{stock_rows}</table>
            </div>
            <div style="flex:2;">
                <div id="chart1" style="height:300px; background:#fff; margin-bottom:20px; border-radius:10px;"></div>
                <div id="chart2" style="height:300px; background:#fff; border-radius:10px;"></div>
            </div>
        </div>
        <script>
            var common = {{ tooltip:{{trigger:'axis'}}, xAxis:{{data:{dates}}}, yAxis:{{scale:true}} }};
            echarts.init(document.getElementById('chart1')).setOption({{
                ...common, title:{{text:'汇率趋势 (Exchange Rate)'}},
                series:[{{name:'USD/CNY', type:'line', data:{usd_vals}, color:'#10b981'}},
                        {{name:'VND/CNY(1k)', type:'line', data:{vnd_vals}, color:'#3b82f6'}}]
            }});
            echarts.init(document.getElementById('chart2')).setOption({{
                ...common, title:{{text:'机票价格与股市指数趋势'}},
                series:[{{name:'Flight($)', type:'line', data:{flt_vals}, color:'#f59e0b'}},
                        {{name:'VN-Index', type:'line', data:{idx_vals}, color:'#ef4444'}}]
            }});
        </script>
    </body></html>"""
    with open("finance.html", "w", encoding="utf-8") as f:
        f.write(finance_html)

if __name__ == "__main__":
    hn_news = fetch_hn_tech()
    world_news = fetch_world_news()
    fin_data = fetch_finance()
    flight_price = fetch_flight_price()
    
    update_db_and_pages(hn_news, world_news, fin_data, flight_price)
    print("✅ 所有看板及历史曲线图已更新！")
