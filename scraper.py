import os
import requests
from datetime import datetime

def fetch_kickstarter_projects():
    print("=== 1. 启动数据采集流程 ===")
    
    # 这里的名字 MY_PROXY_URL 必须与 main.yml 里的 env 名字一致
    proxy_url = os.environ.get('MY_PROXY_URL')
    
    if not proxy_url:
        print("❌ 错误：未读取到环境变量。请检查 GitHub Secret 名称是否确实为 PROXY_URL。")
        return None

    # 打印前缀以确认格式（http://...）
    print(f"📡 代理配置已读取，准备连接...")

    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }

    # 针对 2026 年环境优化的请求参数
    url = "https://www.kickstarter.com/discover/advanced.json?category_id=16&sort=newest&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.kickstarter.com/discover"
    }
    
    try:
        print("📡 正在穿透代理连接目标服务器...")
        # 增加 timeout 防止 GitHub Action 任务卡死
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        
        print(f"📡 服务器响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            projects = data.get('projects', [])
            print(f"✅ 成功！拿到 {len(projects)} 个项目数据。")
            return projects
        elif response.status_code == 407:
            print("❌ 代理认证失败 (407)：请检查 PROXY_URL 里的用户名密码是否包含特殊字符且格式正确。")
        else:
            print(f"⚠️ 请求未成功，返回代码: {response.status_code}")
            
    except Exception as e:
        print(f"❌ 网络异常或代理地址格式错误: {str(e)}")
    
    return None

def generate_html(projects):
    print("=== 2. 开始生成可视化看板 ===")
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not projects:
        content = f"""
        <div style="text-align:center; padding:50px; background:#fff5f5; border:2px solid #feb2b2; border-radius:12px;">
            <h2 style="color:#c53030;">📡 暂无数据</h2>
            <p>原因：数据采集未成功。请检查 GitHub Actions 日志中的状态码。</p>
            <p style="color:#666;">尝试时间: {now}</p>
        </div>
        """
    else:
        cards = ""
        for p in projects[:12]:
            photo = p.get('photo', {}).get('medium', '')
            name = p.get('name', '未知项目')
            link = p.get('urls', {}).get('web', {}).get('project', '#')
            cards += f"""
            <div style="background:white; padding:15px; border-radius:10px; box-shadow:0 2px 5px rgba(0,0,0,0.1);">
                <img src="{photo}" style="width:100%; border-radius:5px; height:150px; object-fit:cover;">
                <h3 style="font-size:14px; color:#2d3748; height:40px; overflow:hidden;">{name}</h3>
                <a href="{link}" target="_blank" style="color:#3182ce; text-decoration:none; font-size:14px;">查看详情 →</a>
            </div>
            """
        content = f'<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:20px;">{cards}</div>'

    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Apex的潮流监控看板</title></head>
    <body style="font-family:sans-serif; background:#f7fafc; padding:20px;">
        <div style="max-width:1000px; margin:auto;">
            <h1 style="text-align:center; color:#2c5282;">🚀 硬件众筹趋势监控</h1>
            {content}
            <div style="text-align:center; margin-top:40px; color:#a0aec0; border-top:1px solid #e2e8f0; padding-top:20px;">
                最后更新：{now} | 版权归属：@Apex's workspace
            </div>
        </div>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_template)
    print("🚀 index.html 写入完成。")

if __name__ == "__main__":
    p_data = fetch_kickstarter_projects()
    generate_html(p_data)
    print("=== 3. 全部任务已完成 ===")
