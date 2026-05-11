import os
import requests
import urllib.parse
from datetime import datetime

def fetch_kickstarter_projects():
    print("--- 步骤 1: 开始读取环境变量 ---")
    user = os.environ.get('DI_USER')
    password = os.environ.get('DI_PASS')
    host = os.environ.get('DI_HOST')
    
    # 打印变量是否存在（但不打印具体内容，保护隐私）
    print(f"DI_USER 是否获取: {'Yes' if user else 'No'}")
    print(f"DI_PASS 是否获取: {'Yes' if password else 'No'}")
    print(f"DI_HOST 是否获取: {'Yes' if host else 'No'}")

    if not all([user, password, host]):
        print("❌ 故障：环境变量缺失，请检查 GitHub Secrets 设置。")
        return None

    print("--- 步骤 2: 进行 URL 转码与代理组装 ---")
    try:
        encoded_user = urllib.parse.quote_plus(user)
        encoded_pass = urllib.parse.quote_plus(password)
        proxy_url = f"http://{encoded_user}:{encoded_pass}@{host}"
        proxies = {"http": proxy_url, "https": proxy_url}
        print("✅ 代理字符串组装完成。")
    except Exception as e:
        print(f"❌ 转码失败: {e}")
        return None

    print("--- 步骤 3: 发起网络请求 ---")
    url = "https://www.kickstarter.com/discover/advanced.json?category_id=16&sort=magic&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    try:
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        print(f"📡 服务器响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            projects = data.get('projects', [])
            print(f"📦 成功！拿到 {len(projects)} 个项目数据。")
            return projects
        else:
            print(f"⚠️ 请求未成功，返回内容: {response.text[:200]}")
            return None
            
    except Exception as e:
        print(f"❌ 请求环节发生崩溃: {e}")
        return None

def generate_html(projects):
    print("--- 步骤 4: 生成 HTML 文件 ---")
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not projects:
        print("⚠️ 注意：由于没有项目数据，将生成错误提示页面。")
        content = f'<div style="color:red; text-align:center;"><h2>数据抓取为空</h2><p>时间：{now}</p></div>'
    else:
        print("✅ 正在构建项目卡片...")
        content = "<ul>" + "".join([f"<li>{p.get('name')}</li>" for p in projects]) + "</ul>"

    html = f"<html><body>{content}<p>Update: {now}</p></body></html>"
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("🚀 index.html 文件已写入磁盘。")

if __name__ == "__main__":
    print("=== 爬虫任务启动 ===")
    p_data = fetch_kickstarter_projects()
    generate_html(p_data)
    print("=== 任务运行结束 ===")
