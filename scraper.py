import os
import requests
from datetime import datetime
import re

def fetch_data():
    print("=== 目标切换：尝试抓取 ycombinator 硬件项目 ===")
    proxy_url = os.environ.get('MY_PROXY_URL')
    if not proxy_url:
        return None

    proxies = {"http": proxy_url, "https": proxy_url}
    
    # 目标换成 Indiegogo 的探索页面
    url = "https://news.ycombinator.com/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    try:
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        print(f"📡 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            content = response.text
            # Indiegogo 的链接特征通常是 /projects/名称
            links = re.findall(r'href=["\'](/projects/[^?"\']+)', content)
            unique_links = list(dict.fromkeys([l for l in links if len(l.split('/')) >= 3]))
            print(f"✅ 成功！提取到 {len(unique_links)} 个 Indiegogo 项目链接")
            return unique_links
    except Exception as e:
        print(f"❌ 异常: {e}")
    return None

def write_html(links):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    list_items = ""
    if links:
        for link in links[:15]:
            name = link.split('/')[-1].replace('-', ' ').title()
            list_items += f'<li><a href="https://www.indiegogo.com{link}" target="_blank">{name}</a></li>'
    
    html = f"""
    <html><body style="font-family:sans-serif;padding:30px;">
        <h2>Global Hardware monitor (Indiegogo Feed)</h2>
        <p>Last Sync: {now}</p>
        <hr>
        <ul>{list_items if list_items else "<li>No data found - Check source.</li>"}</ul>
    </body></html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    links = fetch_data()
    write_html(links)
