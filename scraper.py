import requests
import os
from datetime import datetime

def fetch_kickstarter_projects():
    # 尝试使用高级搜索接口
    url = "https://www.kickstarter.com/discover/advanced.json?category_id=16&sort=magic&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        # 如果返回不是 200，主动抛出异常
        response.raise_for_status() 
        data = response.json()
        return data.get('projects', [])
    except Exception as e:
        print(f"数据抓取失败: {e}")
        return None # 返回 None 代表彻底失败

def generate_html(projects, error_msg=None):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 如果没有项目数据，生成一个“维护中”或“被拦截”的提示页面
    if projects is None or len(projects) == 0:
        content = f"""
        <div style="text-align:center; padding:50px;">
            <h2>⚠️ 抓取暂时受阻</h2>
            <p>Kickstarter 拦截了来自 GitHub 服务器的请求。</p>
            <p style="color:red;">错误代码: {error_msg if error_msg else "IP 被封锁 (403)"}</p>
            <p><strong>解决建议：</strong> 接入我们在方案二中提到的 <strong>DataImpulse 代理 IP</strong> 即可解决。</p>
            <p><small>尝试更新时间: {now}</small></p>
        </div>
        """
    else:
        cards = ""
        for p in projects[:12]:
            percent = int((p.get('pledged', 0) / p.get('goal', 1)) * 100)
            cards += f"""
            <div style="border:1px solid #ddd; padding:15px; border-radius:10px; background:white;">
                <img src="{p.get('photo', {}).get('medium')}" style="width:100%; border-radius:5px;">
                <h3 style="font-size:16px;">{p.get('name')}</h3>
                <p>进度: <strong>{percent}%</strong></p>
                <a href="{p.get('urls', {}).get('web', {}).get('project')}" target="_blank">查看详情</a>
            </div>
            """
        content = f"""
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:20px;">
            {cards}
        </div>
        """

    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>众筹看板</title></head>
    <body style="font-family:sans-serif; background:#f4f4f4; padding:20px;">
        <h1 style="text-align:center;">🚀 全球硬件众筹动态 (2026)</h1>
        {content}
        <hr><p style="text-align:center; color:#888;">最后更新: {now}</p>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_template)

if __name__ == "__main__":
    projects = fetch_kickstarter_projects()
    generate_html(projects)
    print("网页文件已强制生成。")
