用于追踪每日科技与国际新闻
用于追踪特定航线和特定日期的指定航线机票价格
用于追踪常用汇率与主要股票市场指数变化情况
用于追踪 Kickstarter 上智能硬件 / 软件 / AI / 自动化 领域的每日热门众筹项目

## 看板页面

| 页面 | 说明 | 生成脚本 |
|---|---|---|
| `index.html` | 🏠 技术趋势（Hacker News） | `scraper.py` |
| `news.html` | 🌍 国际要闻（BBC） | `scraper.py` |
| `finance.html` | 📈 金融看板 | `scraper.py` |
| `kickstarter.html` | 💡 众筹热点 Top 5 | `kickstarter_scraper.py` |

四张看板共用 GitHub Actions 同一次定时运行（每日 UTC 01:00）、同一次提交与 Pages 部署。
`kickstarter_scraper.py` 仅依赖 Python 标准库与 curl，且设置了 `continue-on-error`，抓取失败不会阻塞其余看板。

众筹看板数据文件：
- `ks_data.json` —— 最近 7 天完整快照（自动裁剪）
- `ks_history.csv` —— 累计上榜台账（append-only，永不清理）

