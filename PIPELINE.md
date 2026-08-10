# AI 日报 · GitHub Actions 自动更新

每天 **08:00（中国标准时间，= 00:00 UTC）** 自动跑一条流水线：

```
采集素材  →  大模型撰稿  →  渲染页面  →  测试校验  →  提交  →  部署 GitHub Pages
```

免费：公开仓库 Actions 无限时长；私有仓库每月 2000 分钟免费（本用量约每月 ~150 分钟）。

## 管线文件
- `scripts/ai_daily/collect.py` — 抓 Hacker News / GitHub / RSS（仅标准库）
- `scripts/ai_daily/generate.py` — 调大模型把素材写成中文稿件（OpenAI 兼容接口，仅标准库）
- `scripts/ai_daily/render.py` — 套模板渲染 `public/ai-daily.html`
- `scripts/ai_daily/terms.json` — 长期术语知识库
- `data/issues/<date>.json` — 当期稿件（首期为 `2026-08-07.json` 范例 + `2026-08-10.json` 当期）
- `.github/workflows/daily.yml` — 每日定时工作流

## 你只需做一次的设置
> 仓库已推到你的 GitHub 账号后，在 **该仓库** 的 Settings 里操作。

1. **开启 Pages**
   Settings → Pages → Source 选 **GitHub Actions**。
   之后站点地址即 `https://<你的用户名>.github.io/ai-daily-radar/`。

2. **添加 Secrets**（Settings → Secrets and variables → Actions → New repository secret）
   | 名称 | 必填 | 说明 |
   |---|---|---|
   | `LLM_API_KEY` | ✅ 必填 | 你的大模型 API key（如 DeepSeek） |
   | `LLM_BASE_URL` | 可选 | 默认 `https://api.deepseek.com/v1` |
   | `LLM_MODEL` | 可选 | 默认 `deepseek-chat`；用免费模型填对应模型名 |

   > 你在 WorkBuddy 里接入的 `deepseek-v4-flash-free` 若走了第三方代理，
   > 把代理给的 **base_url** 和 **model** 分别填进 `LLM_BASE_URL` / `LLM_MODEL` 即可。

3. **手动触发一次验证**
   Actions → `AI 日报每日自动更新` → **Run workflow**。
   看到绿勾、站点能打开，说明全链路通了。

## 故障兜底
- **没填 `LLM_API_KEY` 或 key 错误** → 自动降级为「纯聚合」：页面照常更新，但只有标题/来源/链接，无中文解读（日志会打印 `[降级]`）。
- 想改更新时间：编辑 `.github/workflows/daily.yml` 的 `cron`（注意是 **UTC**）。
- 想立刻出一期：Actions 页面点 **Run workflow** 即可手动跑。

## 本地调试
```bash
python3 scripts/ai_daily/collect.py --days 3
LLM_API_KEY=xxx python3 scripts/ai_daily/generate.py
python3 scripts/ai_daily/render.py --latest
node --test tests/rendered-html.test.mjs
```
