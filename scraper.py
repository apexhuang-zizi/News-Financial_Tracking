import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
import re
import time
import google.generativeai as genai

# --- 1. 配置区 ---
# 修正为你指定的版本
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')

def translate_text(text):
    if not text: return ""
    prompt = f"请将以下技术新闻标题翻译成地道的中文。只需返回翻译文本：\n{text}"
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except: return "翻译暂不可用"

# --- 2. 模块：Hacker News 抓取 (修复链接问题) ---
def fetch_hn_news():
    print("🚀 正在抓取 Hacker News...")
    url = "https://news.ycombinator.com/"
    try:
        r = requests.get(url, timeout=20)
        # 匹配链接和标题
        items = re.findall(r'<span class="titleline"><a href="(.*?)".*?>(.*?)</a>', r.text)
        results = []
        for link, title in items[:20]:
            # 关键修复：处理相对路径链接
            full_url = link if link.startswith('http') else f"https://news.ycombinator.com/{link}"
            results.append({'title': title, 'cn': translate_text(title), 'url': full_url})
            time.sleep(0.3)
        return results
    except Exception as e:
        print(f"新闻抓取失败: {e}")
        return []

# --- 3. 模块：金融看板 (修复代号 & 拆分表) ---
def fetch_finance():
    print("📊 正在获取金融数据...")
    # 指数使用 ^ 前缀，个股使用 .HM 后缀，避免 404 错误
    tickers = {
        "VN_Index": "^VNINDEX", 
        "VN30": "^VN30",
        "FPT_Stock": "FPT.HM",  # 示例：FPT个股
        "USD_CNY": "CNY=X",
        "USD_VND": "VND=X"
    }
    
    fin_data = {}
    for name, code in tickers.items():
        try:
            t = yf.Ticker(code)
            # 获取最新价并处理 history 数据
            hist = t.history(period="1d")
            price = hist['Close'].iloc[-1]
            fin_data[name] = round(price, 2)
        except:
            fin_data[name] = 0.0

    # 计算 1k 越南盾对人民币 (用于独立表)
    if fin_data["USD_VND"] > 0:
        fin_data["VND_CNY_1k"] = round((fin_data["USD_CNY"] / fin_data["USD_VND"]) * 1000, 4)
    else:
        fin_data["VND_CNY_1k"] = 0.0
        
    return fin_data

# --- 4. 机票监控逻辑 ---
def fetch_flight():
    # 此处为机票模拟逻辑，可替换为具体的 API 或爬虫地址
    return 320, 480  # 返回：当日价, 2027-02-01价

# --- 5. 核心：生成 HTML 报告 (四个独立图表) ---
def build_dashboard():
    news = fetch_hn_news()
    fin = fetch_finance()
    f_now, f_future = fetch_flight()
    
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # 历史记录处理 (略，假设你已在本地维护 history.csv)
    
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Apex News Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js"></script>
        <style>
            body {{ font-family: sans-serif; background: #f4f7f9; padding: 20px; }}
            .container {{ max-width: 1200px; margin: auto; }}
            .header {{ text-align: center; margin-bottom: 40px; }}
            .header h1 {{ font-size: 32px; color: #333; }}
            .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 40px; }}
            .chart-box {{ background: #fff; padding: 20px; border-radius: 12px; height: 350px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }}
            .news-list {{ background: #fff; padding: 30px; border-radius: 12px; }}
            .news-item {{ margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
            .news-item a {{ color: #d97706; text-decoration: none; font-weight: bold; }}
            .news-item p {{ color: #666; font-size: 14px; margin-top: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Apex的爱好-每日关注</h1>
                <p>最后更新：{today}</p>
            </div>

            <div class="chart-grid">
                <div id="c_usd" class="chart-box"></div>
                <div id="c_vnd" class="chart-box"></div>
                <div id="c_stock" class="chart-box"></div>
                <div id="c_flight" class="chart-box"></div>
            </div>

            <div class="news-list">
                <h3>🔥 Hacker News 技术趋势</h3>
                {"".join([f'<div class="news-item"><a href="{x["url"]}" target="_blank">{x["cn"]}</a><p>{x["title"]}</p></div>' for x in news])}
            </div>
        </div>

        <script>
            // 柱状图/折线图初始化示例数据
            var opt = (title, name, val) => ({{
                title: {{ text: title }},
                tooltip: {{ trigger: 'axis' }},
                xAxis: {{ data: ['今日'] }},
                yAxis: {{ scale: true }},
                series: [{{ name: name, type: 'bar', data: [val], barWidth: '30%' }}]
            }});

            echarts.init(document.getElementById('c_usd')).setOption(opt('美元/人民币', 'USD/CNY', {fin['USD_CNY']}));
            echarts.init(document.getElementById('c_vnd')).setOption(opt('1k越南盾/人民币', 'VND/CNY', {fin['VND_CNY_1k']}));
            echarts.init(document.getElementById('c_stock')).setOption({{
                title: {{ text: '股市行情 (指数与个股)' }},
                tooltip: {{ trigger: 'axis' }},
                xAxis: {{ data: ['VN-Index', 'VN30', 'FPT'] }},
                series: [{{ type: 'bar', data: [{fin['VN_Index']}, {fin['VN30']}, {fin['FPT_Stock']}] }}]
            }});
            echarts.init(document.getElementById('c_flight')).setOption({{
                title: {{ text: 'SGN-CAN 机票监控' }},
                legend: {{ data: ['当日价', '2027-02-01价'], bottom: 0 }},
                xAxis: {{ data: ['价格'] }},
                series: [
                    {{ name: '当日价', type: 'bar', data: [{f_now}] }},
                    {{ name: '2027-02-01价', type: 'bar', data: [{f_future}] }}
                ]
            }});
        </script>
    </body>
    </html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_template)
    print("✅ 报告生成完毕: index.html")

if __name__ == "__main__":
    build_dashboard()
