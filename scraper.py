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

# --- 配置区 ---
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

def translate_text(text, is_tech=True):
    """通用 Gemini 翻译函数"""
    if not text: return ""
    domain = "技术新闻" if is_tech else "国际新闻"
    prompt = f"你是一个专业的{domain}翻译。请将以下标题翻译成地道的中文。只需返回翻译结果：\n\n{text}"
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except:
        return ""

# --- 1. 国际要闻抓取 (BBC RSS) ---
def fetch_intl_news():
    print("🌍 抓取国际要闻...")
    url = "http://feeds.bbci.co.uk/news/world/rss.xml"
    try:
        r = requests.get(url, timeout=20)
        root = ET.fromstring(r.content)
        news_items = []
        for item in root.findall('.//item')[:10]:
            title = item.find('title').text
            link = item.find('link').text
            cn_title = translate_text(title, is_tech=False)
            news_items.append({'en': title, 'cn': cn_title, 'url': link})
            time.sleep(0.5) # 频率控制
        return news_items
    except Exception as e:
        print(f"新闻抓取失败: {e}")
        return []

# --- 2. 金融数据抓取 (汇率 & 越南股市) ---
def fetch_finance():
    print("📈 抓取金融数据...")
    # 定义代码映射
    tickers = {
        "USD/CNY": "USDCNY=X",
        "VND/CNY": "VNDCNY=X",
        "FPT (FPT)": "FPT.VN",
        "Vietcombank (VCB)": "VCB.VN",
        "Vinamilk (VNM)": "VNM.VN",
        "Hoa Phat (HPG)": "HPG.VN",
        "Mobile World (MWG)": "MWG.VN",
        "BIDV (BID)": "BID.VN",
        "Vinhomes (VHM)": "VHM.VN",
        "Masan (MSN)": "MSN.VN",
        "PV Gas (GAS)": "GAS.VN",
        "SSI Securities (SSI)": "SSI.VN",
        "VN-Index": "^VNINDEX",
        "VN30 Index": "^VN30"
    }
    results = {}
    for name, code in tickers.items():
        try:
            t = yf.Ticker(code)
            # 获取最新价格，若 yfinance 获取不到，则设为 0
            price = t.fast_info['last_price']
            results[name] = round(price, 4 if "CNY" in name else 2)
        except:
            results[name] = 0.0
    return results

# --- 3. 机票监控 (SGN - CAN) ---
def fetch_flight_price():
    print("✈️ 抓取机票价格...")
    # 这里通过搜索引擎结果进行“最佳努力”抓取，避免 headless browser 导致的 403
    url = "https://www.google.com/search?q=flight+price+SGN+to+CAN+direct+one+way"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        # 正则寻找包含 $ 符号的金额数字
        prices = re.findall(r'\$(\d{2,4})', r.text)
        if prices:
            return min([int(p) for p in prices])
    except:
        pass
    return 0

# --- 4. 数据库持久化 ---
def update_database(finance_data, flight_price):
    today = datetime.now().strftime('%Y-%m-%d')
    new_data = {"Date": today, "Flight_Price": flight_price, **finance_data}
    
    file = "history.csv"
    if os.path.exists(file):
        df = pd.read_csv(file)
        # 覆盖当天旧数据，合并新数据
        df = df[df['Date'] != today]
        df = pd.concat([df, pd.DataFrame([new_data])], ignore_index=True)
    else:
        df = pd.DataFrame([new_data])
    
    # 只保留最近 30 天
    df = df.tail(30)
    df.to_csv(file, index=False)
    return df

# --- 5. 网页生成模块 ---
def generate_pages(hn_news, intl_news, finance_data, history_df):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # 导航栏 HTML
    nav_html = """
    <div style="margin-bottom:20px; padding:15px; background:white; border-radius:8px;">
        <a href="index.html" style="margin-right:20px; text-decoration:none; color:#ff6600; font-weight:bold;">🏠 HN Tech / 技术趋势</a>
        <a href="news.html" style="margin-right:20px; text-decoration:none; color:#2563eb; font-weight:bold;">🌍 World News / 国际要闻</a>
        <a href="finance.html" style="text-decoration:none; color:#059669; font-weight:bold;">📈 Finance / 金融看板</a>
    </div>
    """

    # --- Page 1: index.html (Hacker News) ---
    hn_list = "".join([f"<li><a href='{link}'>{title}</a><br><small style='color:gray'>{translate_text(title)}</small></li>" for link, title in hn_news[:15]])
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(f"<html><body style='background:#f9fafb; font-family:sans-serif; padding:20px;'><div style='max-width:800px; margin:auto;'>{nav_html}<h2>Technical Trends / 技术趋势</h2><ul>{hn_list}</ul><p><small>Updated: {now}</small></p></div></body></html>")

    # --- Page 2: news.html (BBC News) ---
    intl_list = "".join([f"<li style='margin-bottom:15px;'><a href='{n['url']}'>{n['en']}</a><br><b style='color:#1e40af'>{n['cn']}</b></li>" for n in intl_news])
    with open("news.html", "w", encoding="utf-8") as f:
        f.write(f"<html><body style='background:#f3f4f6; font-family:sans-serif; padding:20px;'><div style='max-width:800px; margin:auto;'>{nav_html}<h2>International News / 国际要闻 (BBC)</h2><ul>{intl_list}</ul></div></body></html>")

    # --- Page 3: finance.html (Dashboard + Charts) ---
    stock_rows = "".join([f"<tr><td style='padding:8px; border-bottom:1px solid #eee;'>{k}</td><td style='text-align:right; font-weight:bold;'>{v}</td></tr>" for k, v in finance_data.items()])
    
    # 提取图表数据 (最近15天)
    chart_df = history_df.tail(15)
    dates = chart_df['Date'].tolist()
    usd_vals = chart_df['USD/CNY'].tolist()

    finance_html = f"""
    <html>
    <head>
        <script src="https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js"></script>
    </head>
    <body style='background:#f0fdf4; font-family:sans-serif; padding:20px;'>
        <div style='max-width:800px; margin:auto;'>
            {nav_html}
            <div style="background:white; padding:20px; border-radius:12px; margin-bottom:20px;">
                <h2 style="color:#059669;">Finance Dashboard / 金融看板</h2>
                <div style="display:flex; justify-content:space-between;">
                    <table style="width:45%; border-collapse:collapse;">{stock_rows}</table>
                    <div style="width:50%; background:#fef3c7; padding:15px; border-radius:8px; height:fit-content;">
                        <h3>✈️ Flight Price / 机票监控</h3>
                        <p>SGN - CAN (Direct): <b style="font-size:24px; color:#b45309;">${history_df.iloc[-1]['Flight_Price']}</b></p>
                        <small>Ho Chi Minh to Guangzhou / 胡志明-广州</small>
                    </div>
                </div>
            </div>
            <div style="background:white; padding:20px; border-radius:12px;">
                <div id="main" style="width:100%; height:300px;"></div>
            </div>
        </div>
        <script>
            var myChart = echarts.init(document.getElementById('main'));
            myChart.setOption({{
                title: {{ text: 'USD/CNY Trend / 美元汇率趋势' }},
                tooltip: {{ trigger: 'axis' }},
                xAxis: {{ data: {dates} }},
                yAxis: {{ scale: true }},
                series: [{{ name: 'USD/CNY', type: 'line', data: {usd_vals}, smooth: true, color: '#059669' }}]
            }});
        </script>
    </body>
    </html>
    """
    with open("finance.html", "w", encoding="utf-8") as f:
        f.write(finance_html)

# --- 原有 HN 抓取逻辑简化适配 ---
def fetch_hn_simple():
    try:
        r = requests.get("https://news.ycombinator.com/", timeout=15)
        pattern = r'<span class="titleline"><a href="(.*?)".*?>(.*?)</a>'
        return re.findall(pattern, r.text)
    except: return []

if __name__ == "__main__":
    # 1. 抓取所有维度数据
    hn_news = fetch_hn_simple()
    intl_news = fetch_intl_news()
    fin_data = fetch_finance()
    f_price = fetch_flight_price()
    
    # 2. 更新数据库并生成页面
    history_df = update_database(fin_data, f_price)
    generate_pages(hn_news, intl_news, fin_data, history_df)
    
    print("✅ 任务圆满完成！")
