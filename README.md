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

前三张由 GitHub Actions（`.github/workflows/main.yml`，每日 UTC 01:00）自动抓取并发布。

众筹看板由**本机**执行 `python kickstarter_scraper.py --push` 更新：抓取 → 写数据 →
渲染 → `git pull --rebase` → commit → push；推送到 main 后，`.github/workflows/pages.yml`
会自动发布 Pages（约 20 秒），且次日主工作流的提交与部署也会带上它。

> 为什么众筹数据不在 CI 里抓：实测 GitHub Actions 的机房出口 IP 被 Cloudflare 全线拒绝，
> 连 `discover` 的 JSON 接口都返回 403 `Just a moment...`，而档次价格必须解析详情页原始
> HTML 才能拿到。本机（住宅出口 IP）抓取完全正常，因此保留完整数据保真度。

众筹看板数据文件：
- `ks_data.json` —— 最近 7 天完整快照（自动裁剪）
- `ks_history.csv` —— 累计上榜台账（append-only，永不清理）

脚本内置三级网络降级（代理 → 直连 → r.jina.ai）与数据质量闸门：若档次数据缺失，
保留上一次完整结果并在页面顶部显示告警条，不会用残缺数据覆盖。

