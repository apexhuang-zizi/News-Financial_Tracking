import os
import requests
from datetime import datetime
import time
import random
import re

def fetch_data():
    print("=== 执行全浏览器伪装抓取 ===")
    proxy_url = os.environ.get('MY_PROXY_URL')
    if not proxy_url:
        return None

    proxies = {"http": proxy_url, "https": proxy_url}
    
    # 回归最稳妥的搜索页面
    url = "https://www.google.com/search?q=kickstarter+hardware"
    
    # 这是一套从 2026 年最新版 Chrome 中完全复刻的请求头
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Connection": "keep-alive"
    }
    
    try:
        # 增加随机停顿，防止频率过快
        time.sleep(random.uniform(5, 10))
        
        # 使用 Session 对象可以自动处理一些 Cookie 逻辑，增加成功率
        session = requests.Session()
        response = session.get(url, headers=headers, proxies=proxies, timeout=30)
        
        print(f"📡 最终尝试状态码: {response.status_code}")
        
        if response.status_code == 200:
            content = response.text
            # 正则匹配项目链接（这次使用更宽泛的规则）
            raw_links = re.findall(r'href=["\'](/projects/[^?"\']+)', content)
            unique_links = list(dict.fromkeys([l for l in raw_links if len(l.split('/')) >= 4]))
            print(f"✅ 成功抓取！提取到 {len(unique_links)} 个链接")
            return unique_links
        else:
            print(f"❌ 依然被拒，状态码: {response.status_code}")
            if response.status_code == 406:
                print("💡 建议：如果 406 持续存在，说明 DataImpulse 的 IP 段已被封死。")
                
    except Exception as e:
        print(f"❌ 异常详情: {e}")
    return None

def write_html(links):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    list_items = ""
    if links:
        for link in links[:15]:
            name = link.split('/')[-1].replace('-', ' ').title()
            list_items += f'<li><a href="https://www.kickstarter.com{link}" target="_blank">{name}</a></li>'
    
    html = f"""
    <html><body style="font-family:sans-serif;padding:30px;">
    <h2>Data Feed</h2><p>Last Sync: {now}</p><ul>{list_items if list_items else "<li>No Data - Verification Required</li>"}</ul>
    </body></html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    data = fetch_data()
    write_html(data)
