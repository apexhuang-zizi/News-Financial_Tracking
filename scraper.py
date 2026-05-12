import os
import requests
from datetime import datetime
import re
import time
import google.generativeai as genai

# --- Gemini 配置 ---
# 请确保已在 GitHub Secrets 中设置 GEMINI_API_KEY
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

def translate_title(english_title):
    """调用 Gemini API 将标题翻译为中文"""
    if not english_title:
        return ""
    
    prompt = f"你是一个专业的软件工程师和技术翻译。请将以下技术新闻标题翻译成地道的中文，保持术语准确（如保留 Python, AI 等词汇）。只需返回翻译后的文字，不要有任何解释：\n\n{english_title}"
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ 翻译执行异常: {e}")
        return ""

def fetch_data():
    print("=== 最终链路验证：抓取 Hacker News ===")
    
    # 自动获取你在 GitHub Secrets 设置的代理
    proxy_url = os.environ.get('MY_PROXY_URL')
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    
    # 目标 URL
    url = "https://news.ycombinator.com/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        # 执行请求
        response = requests.get(url, headers=headers, proxies=proxies, timeout=20)
        print(f"📡 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            content = response.text
            # 提取新闻标题和链接
            pattern = r'<span class="titleline"><a href="(.*?)".*?>(.*?)</a>'
            items = re.findall(pattern, content)
            
            print(f"✅ 成功！抓取到 {len(items)} 条真实新闻")
            return items
    except Exception as e:
        print(f"❌ 抓取异常: {e}")
    return None

def write_html(items):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    list_items = ""
    
    if items:
        count = 0
        for link, title in items[:20]:
            count += 1
            print(f"正在处理第 {count}/20 条: {title[:30]}...")
            
            # 1. 处理相对路径链接
            full_url = link if link.startswith('http') else f"https://news.ycombinator.com/{link}"
            
            # 2. 调用 Gemini 进行翻译
            cn_title = translate_title(title)
            
            # 3. 构造翻译后的显示内容
            # 如果翻译成功，则在英文下方显示灰色中文；如果失败，则只显示英文
            translation_html = f"<div style='color: #6b7280; font-size: 13px; margin-top: 4px;'>{cn_title}</div>" if cn_title else ""
            
            list_items += f"""
            <li style="margin-bottom: 20px; border-bottom: 1px solid #f3f4f6; pb: 10px;">
                <a href="{full_url}" target="_blank" style="color: #d97706; text-decoration: none; font-weight: 500; font-size: 16px;">
                    {title}
                </a>
                {translation_html}
            </li>"""
            
            # 4. 频率控制：由于 Gemini 免费版有限制，每条之间休息 1 秒
            time.sleep(1)
    else:
        list_items = "<li>⚠️ 暂时没有抓取到数据，请检查 Actions 日志。</li>"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Data Monitor</title></head>
    <body style="font-family: system-ui, -apple-system, sans-serif; background: #fffaf0; padding: 40px;">
        <div style="max-width: 700px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #ff6600; margin-top: 0;">Hacker News Tech Trends (With AI Translation)</h2>
            <p style="color: #71717a; font-size: 14px;">最后更新时间: {now}</p>
            <hr style="border: 0; border-top: 1px solid #fee2e2; margin: 20px 0;">
            <ul style="padding-left: 0; list-style: none;">
                {list_items}
            </ul>
        </div>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("🚀 index.html 页面已更新（包含 AI 翻译）")

if __name__ == "__main__":
    data = fetch_data()
    write_html(data)
