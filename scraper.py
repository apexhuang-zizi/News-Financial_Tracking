import os
import requests
from datetime import datetime
import re

def fetch_data():
    print("=== 最终链路验证：抓取 Hacker News ===")
    
    # 自动获取你在 GitHub Secrets 设置的代理
    proxy_url = os.environ.get('MY_PROXY_URL')
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    
    # 绝对纯净的 URL，没有任何括号或多余字符
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
            # HN 的结构非常固定：<span class="titleline"><a href="URL">TITLE</a></span>
            pattern = r'<span class="titleline"><a href="(.*?)".*?>(.*?)</a>'
            items = re.findall(pattern, content)
            
            print(f"✅ 成功！抓取到 {len(items)} 条真实新闻")
            return items
    except Exception as e:
        print(f"❌ 运行异常: {e}")
    return None

def write_html(items):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    list_items = ""
    
    if items:
        for link, title in items[:20]:
            # 处理相对路径
            full_url = link if link.startswith('http') else f"https://news.ycombinator.com/{link}"
            list_items += f"""
            <li style="margin-bottom: 15px;">
                <a href="{full_url}" target="_blank" style="color: #d97706; text-decoration: none; font-weight: 500;">
                    {title}
                </a>
            </li>"""
    else:
        list_items = "<li>⚠️ 暂时没有抓取到数据，请检查 Actions 日志。</li>"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Data Monitor</title></head>
    <body style="font-family: system-ui, -apple-system, sans-serif; background: #fffaf0; padding: 40px;">
        <div style="max-width: 700px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #ff6600; margin-top: 0;">Hacker News Tech Trends</h2>
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
    print("🚀 index.html 页面已更新")

if __name__ == "__main__":
    data = fetch_data()
    write_html(data)
