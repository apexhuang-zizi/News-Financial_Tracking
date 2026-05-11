import os
import requests
from datetime import datetime

def fetch_kickstarter_projects():
    # 自动从 GitHub 环境变量读取代理，若本地运行没设变量则为 None
    proxy_url = os.environ.get('MY_PROXY')
    
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    } if proxy_url else None

    # Kickstarter 科技类接口
    url = "https://www.kickstarter.com/discover/advanced.json?category_id=16&sort=magic&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    try:
        response = requests.get(url, headers=headers, proxies=proxies, timeout=25)
        response.raise_for_status()
        print("✅ 数据抓取成功！" if not proxies else "✅ 代理访问成功！")
        return response.json().get('projects', [])
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return None

def generate_html(projects):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not projects:
        # 失败时的占位内容
        content = """
        <div style="text-align:center; padding:50px; background:white; border-radius:15px;">
            <h2 style="color:#e74c3c;">📡 信号暂时中断</h2>
            <p>目前无法获取 Kickstarter 数据。可能原因：代理配置有误或 IP 流量耗尽。</p>
            <p>请检查 GitHub Secrets 中的 <b>PROXY_URL</b> 格式。</p>
        </div>
        """
    else:
        cards = ""
        for p in projects[:12]:
            goal = p.get('goal', 1)
            pledged = p.get('pledged', 0)
            percent = int((pledged / goal) * 100)
            cards += f"""
            <div style="background:white; padding:15px; border-radius:12px; box-shadow:0 4px 6px rgba(0,0,0,0.05);">
                <img src="{p.get('photo', {}).get('medium')}" style="width:100%; border-radius:8px; height:180px; object-fit:cover;">
                <h3 style="font-size:16px; height:45px; overflow:hidden; margin:10px 0;">{p.get('name')}</h3>
                <div style="background:#eee; height:8px; border-radius:4px;">
                    <div style="background:#27ae60; width:{min(percent, 100)}%; height:100%; border-radius:4px;"></div>
                </div>
                <p style="font-size:14px; color:#666;">进度: <b>{percent}%</b></p>
                <a href="{p.get('urls', {}).get('web', {}).get('project')}" target="_blank" 
                   style="display:block; text-align:center; background:#1a365d; color:white; padding:8px; border-radius:5px; text-decoration:none; margin-top:10px;">
                   查看灵感
                </a>
            </div>
            """
        content = f"""<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:25px;">{cards}</div>"""

    html_template = f"""
    <!DOCTYPE html>
    <html lang="zh">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>众筹灵感监控站</title>
    </head>
    <body style="font-family:'Segoe UI', sans-serif; background:#f0f2f5; padding:30px; color:#333;">
        <header style="text-align:center; margin-bottom:40px;">
            <h1 style="color:#1a365d; margin-bottom:5px;">🚀 全球硬件众筹看板</h1>
            <p style="color:#7f8c8d;">同步国际最前沿智能家居与极客产品灵感</p>
        </header>
        <div style="max-width:1200px; margin:auto;">{content}</div>
        <footer style="text-align:center; margin-top:50px; color:#95a5a6; font-size:12px;">
            <p>最后更新时间：{now} (UTC)</p>
            <p>@ 版权归属：KINGWOOD VIETNAM</p>
        </footer>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_template)

if __name__ == "__main__":
    data = fetch_kickstarter_projects()
    generate_html(data)
