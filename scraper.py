import os
import requests
from datetime import datetime
import time
import re

def fetch_data():
    print("=== 正在通过 Atom 订阅源获取数据 ===")
    proxy_url = os.environ.get('MY_PROXY_URL')
    if not proxy_url:
        print("❌ 错误：未检测到 PROXY_URL")
        return None

    proxies = {"http": proxy_url, "https": proxy_url}
    
    # 使用后门 RSS/Atom 地址，防御等级更低
    url = "https://www.kickstarter.com/discover/advanced.atom?category_id=16&sort=newest"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        # 解决 406 报错的关键：明确告诉服务器我们要 atom+xml 格式
        "Accept": "application/atom+xml, application/xml, text/xml, */*"
    }
    
    try:
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        print(f"📡 服务器响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            content = response.text
            print(f"✅ 成功获取数据，长度: {len(content)} 字符")
            
            # 使用正则抓取标题和链接
            # Atom 源的格式很固定：<title>项目名</title> 和 <link href="链接"/>
            titles = re.findall(r'<title>(.*?)</title>', content)
            links = re.findall(r'<link [^>]*href="(.*?)"', content)
            
            # 过滤掉第一个标题（通常是频道标题 "Kickstarter » Discover"）
            results = []
            for t, l in zip(titles[1:], links[1:]):
                if '/projects/' in l:
                    results.append({"title": t, "link": l})
            
            print(f"✅ 成功提取到 {len(results)} 个最新项目")
            return results
        else:
            print(f"⚠️ 无法获取数据，状态码: {response.status_code}")
            
    except Exception as e:
        print(f"❌ 异常: {e}")
    return None

def write_html(projects):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    list_items = ""
    
    if projects:
        for p in projects[:15]:
            list_items += f"""
            <li style="margin-bottom: 12px; list-style: none;">
                <span style="color: #10b981;">●</span>
                <a href="{p['link']}" target="_blank" style="color: #1f2937; text-decoration: none; font-weight: 500; font-size: 16px;">
                    {p['title']}
                </a>
            </li>"""
    else:
        list_items = "<li>⚠️ 暂无更新，请检查日志。</li>"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Trends Monitor</title></head>
    <body style="font-family: system-ui, sans-serif; background: #f3f4f6; padding: 30px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
            <h2 style="margin-top: 0; color: #111827;">Global Trends Monitor</h2>
            <p style="color: #6b7280; font-size: 14px;">Sync Time: {now}</p>
            <hr style="border: 0; border-top: 1px solid #e5e7eb; margin: 20px 0;">
            <ul style="padding: 0;">
                {list_items}
            </ul>
        </div>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    data = fetch_data()
    write_html(data)
