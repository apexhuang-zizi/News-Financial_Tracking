import os
import requests
from datetime import datetime
import time
import random
import re

def fetch_data():
    print("=== 切换至网页解析模式 (绕过 API 拦截) ===")
    proxy_url = os.environ.get('MY_PROXY_URL')
    if not proxy_url:
        return None

    proxies = {"http": proxy_url, "https": proxy_url}
    
    # 尝试访问网页版搜索结果，而不是 JSON API
    # url = "https://www.kickstarter.com/discover/advanced?category_id=16&sort=newest"
    # url = "https://en.wikipedia.org/wiki/Main_Page"
    url = "https://www.kickstarter.com/discover/advanced.atom?category_id=16&sort=newest"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0"
    }
    
    try:
        # 随机增加一个更长的等待，模拟真人打开网页
        time.sleep(random.uniform(3, 7))
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        print(f"📡 网页响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            # 使用正则简单抓取项目名称和链接（因为没装 BeautifulSoup，我们用正则最快）
            content = response.text
            # 匹配项目链接和名称的简化正则
            links = re.findall(r'href="(/projects/[^?"]+)', content)
            # 去重
            unique_links = list(dict.fromkeys(links))
            print(f"✅ 成功从网页提取到 {len(unique_links)} 个链接")
            return unique_links
        elif response.status_code == 403:
            print("❌ 代理被彻底封锁。原因：DataImpulse 的 IP 质量已被目标站列入黑名单。")
    except Exception as e:
        print(f"❌ 异常: {e}")
    return None

def write_html(links):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    list_items = ""
    if links:
        for link in links[:15]:
            # 提取名字
            name = link.split('/')[-1].replace('-', ' ').title()
            full_url = f"https://www.kickstarter.com{link}"
            list_items += f'<li><a href="{full_url}" target="_blank">{name}</a></li>'
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Trends Monitor</title></head>
    <body style="font-family:sans-serif; padding:20px;">
        <h2>Global Hardware Trends</h2>
        <p>Last Update: {now}</p>
        <hr>
        <ul>{list_items if list_items else "<li>Wait for IP Rotation...</li>"}</ul>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    links = fetch_data()
    write_html(links)
