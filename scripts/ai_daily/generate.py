#!/usr/bin/env python3
"""用大模型把当日素材写成 AI 日报稿件 JSON（GitHub Actions 用）。

仅依赖标准库（urllib），通过 OpenAI 兼容 Chat Completions 接口调用。

环境变量：
  LLM_API_KEY   必填；缺失则回退到简单聚合（无中文解读，但页面照常更新）
  LLM_BASE_URL  默认 https://api.deepseek.com/v1
  LLM_MODEL     默认 deepseek-chat
  ISSUE_DATE    可选，默认今天（CST）

产物：data/issues/<date>.json
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
ISSUE_DIR = ROOT / "data" / "issues"
TERMS_DB = Path(__file__).parent / "terms.json"
EXAMPLE = ISSUE_DIR / "2026-08-07.json"

CST = timezone(timedelta(hours=8))
NOTE_ORDER = ["这是", "不同", "适合", "今天看", "注意"]

SYSTEM_PROMPT = """你是一位资深的中文科技编辑，负责把零散的 AI 资讯素材写成一期《AI 日报》稿件。
风格要求：
- 中文撰写，信息密度高，直给事实与实质影响，不堆砌标题党话术。
- 每条资讯写成 2 段左右的解读（paras），并写一段"为什么值得关心"（why）。
- 可在正文用 [[slug]] 标注术语、用 `反引号` 标注行内代码（如 `MCP`）。
- 若某一术语不在下方"已收录术语"白名单里，且你确实要用，则必须在输出的 terms 对象里为它补一条 kind:temp 的解释。
- 不要编造你无法从素材中确认的具体数字、日期、人名或引语；拿不准就措辞谨慎。

严格输出如下 JSON（不要任何额外文字、不要 markdown 代码块）：
{
  "headline": "一句话本期主题（30 字内）",
  "news": [
    {
      "source": "来源与热度，例如 'Hacker News · 839 分 / 906 评' 或 'Google Blog / Hacker News'",
      "title": "标题",
      "url": "原文链接",
      "paras": ["第一段解读", "第二段解读"],
      "why": "为什么值得关心"
    }
  ],
  "repos": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "category": "一句话归类",
      "stars": "数字字符串，如 '238,477'（未知写 '—'）",
      "growth": "增量说明，如 '本周 +1.2k' 或 '—'",
      "lang": "主要语言或 '—'",
      "license": "许可证或 '—'",
      "notes": {
        "这是": "……",
        "不同": "……（高亮项，写它与同类最关键的差异）",
        "适合": "……",
        "今天看": "……（为什么这期值得提）",
        "注意": "……（风险或前提）"
      }
    }
  ],
  "terms": {
    "slug": {"display": "显示名", "title": "标题", "kind": "temp", "plain": "简明解释", "why": "为什么提它"}
  },
  "pending": {"出现多次值得收进知识库的词": 3}
}
约束：news 5~8 条；repos 3~4 个；每条 news 必须有 title/url/why/paras（paras 非空）；每条 repo 必须有 name/url(必须 https://github.com/ 开头)/notes（5 项至少填一项）。date 与 range 由系统填写，你无需输出。"""


def call_llm(api_key: str, base_url: str, model: str, user_content: str) -> dict | None:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=150, context=ssl.create_default_context()) as resp:
            payload = json.loads(resp.read())
        content = payload["choices"][0]["message"]["content"]
        # 容错：模型可能用 ```json 包裹，或前后带文字
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            content = m.group(0)
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        print(f"  [LLM 调用失败] {exc}", file=sys.stderr)
        return None


def condense(raw: dict) -> str:
    lines = []
    lines.append("=== Hacker News 热帖（按热度）===")
    for h in raw.get("hackernews", [])[:18]:
        lines.append(f"- {h['title']} | {h['points']}分 {h['comments']}评 | {h['url']}")
    lines.append("\n=== GitHub 项目（按星标/增量）===")
    for g in raw.get("github", [])[:15]:
        topics = ",".join(g.get("topics", [])[:6])
        lines.append(f"- {g['name']} | {g['stars']}★ | {g['lang'] or '-'} | {g['license'] or '-'} | {g['description']}")
        if topics:
            lines.append(f"    topics: {topics}")
    lines.append("\n=== RSS 文章 ===")
    for r in raw.get("rss", [])[:12]:
        lines.append(f"- [{r['source']}] {r['title']} | {r['url']}")
    return "\n".join(lines)


def build_user_prompt(raw: dict, terms_db: dict, example: dict) -> str:
    whitelist = ", ".join(sorted(terms_db.keys()))
    ex_news = json.dumps(example["news"][0], ensure_ascii=False, indent=2)
    ex_repo = json.dumps(example["repos"][0], ensure_ascii=False, indent=2)
    return f"""# 已收录术语白名单（用 [[slug]] 可直接引用，显示绿色；不在其中的若要用请补到 terms）
{whitelist}

# 今日素材（请用这些真实信息撰写，不要编造素材里没有的具体事实）
{condense(raw)}

# 结构与时感范例（只作格式与语感参考，不要照抄内容）
新闻范例：
{ex_news}

GitHub 项目范例：
{ex_repo}

请输出本期《AI 日报》稿件 JSON。news 5~8 条、repos 3~4 个，主题可收拢到 1~2 条主线。"""


def sanitize(issue: dict, date: str, terms_db: dict) -> dict:
    import re
    issue.setdefault("date", date)
    # 保证 headline 存在，否则 render.py 会 KeyError
    if not issue.get("headline"):
        first = (issue.get("news") or [{}])[0].get("title") if issue.get("news") else ""
        issue["headline"] = first or "AI 日报 · 今日速览"
    slug_re = re.compile(r"\[\[([a-z0-9\-]+)")
    used = set()
    for block in (issue.get("news") or []) + (issue.get("repos") or []):
        text = json.dumps(block, ensure_ascii=False)
        used.update(slug_re.findall(text))

    terms = issue.get("terms") or {}
    # 未知 slug：自动补一条 temp 占位，避免渲染报错
    for slug in used:
        if slug in terms_db:
            continue
        if slug not in terms:
            terms[slug] = {
                "display": slug, "title": slug, "kind": "temp",
                "plain": "（自动生成的临时解释，待补充）",
                "why": "",
            }
    issue["terms"] = terms

    # 数量约束
    news = [n for n in issue.get("news") or [] if n.get("title") and n.get("url") and n.get("paras")]
    repos = [r for r in issue.get("repos") or [] if r.get("name") and str(r.get("url", "")).startswith("https://github.com/")]
    issue["news"] = news[:8] if len(news) > 8 else news
    issue["repos"] = repos[:4] if len(repos) > 4 else repos
    issue["pending"] = issue.get("pending") or {}
    return issue


def fallback(raw: dict, date: str) -> dict:
    print("  [降级] 使用简单聚合（无大模型解读）", file=sys.stderr)
    news = []
    for h in raw.get("hackernews", [])[:8]:
        news.append({
            "source": f"Hacker News · {h['points']}分 / {h['comments']}评",
            "title": h["title"],
            "url": h["url"],
            "paras": [h["title"] + ("。" if not h["title"].endswith(("。", "！", "？")) else "")],
            "why": "（自动聚合，未做人工解读，请补充。）",
        })
    repos = []
    for g in raw.get("github", [])[:4]:
        repos.append({
            "name": g["name"], "url": g["url"],
            "category": "GitHub 热门", "stars": str(g["stars"]),
            "growth": "—", "lang": g.get("lang") or "—",
            "license": g.get("license") or "—",
            "notes": {"这是": g.get("description") or "—", "今天看": "本期自动聚合入选。"},
        })
    return {
        "headline": "AI 日报 · 今日速览（自动聚合，未做人工解读）",
        "news": news,
        "repos": repos,
        "terms": {},
        "pending": {},
    }


def main() -> int:
    date = os.environ.get("ISSUE_DATE") or datetime.now(CST).strftime("%Y-%m-%d")
    rng_start = (datetime.now(CST) - timedelta(days=3)).strftime("%Y-%m-%d")
    print(f"撰稿 {date} 期")

    raw_path = RAW_DIR / f"{date}.json"
    if not raw_path.exists():
        # 退而求其次用最新原始素材
        candidates = sorted(RAW_DIR.glob("*.json"))
        if not candidates:
            print("找不到任何原始素材，请先运行 collect.py", file=sys.stderr)
            return 1
        raw_path = candidates[-1]
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    terms_db = json.loads(TERMS_DB.read_text(encoding="utf-8")) if TERMS_DB.exists() else {}
    example = json.loads(EXAMPLE.read_text(encoding="utf-8")) if EXAMPLE.exists() else {"news": [], "repos": []}

    api_key = os.environ.get("LLM_API_KEY")
    issue = None
    if api_key:
        print("  · 调用大模型撰稿 …")
        user = build_user_prompt(raw, terms_db, example)
        out = call_llm(
            api_key,
            os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            os.environ.get("LLM_MODEL", "deepseek-chat"),
            user,
        )
        if out:
            issue = out
    if issue is None:
        issue = fallback(raw, date)

    issue = sanitize(issue, date, terms_db)
    issue["date"] = date
    issue["range"] = f"{rng_start} → {date}"
    issue["updated"] = datetime.now(CST).strftime("%m-%d %H:%M")

    ISSUE_DIR.mkdir(parents=True, exist_ok=True)
    out = ISSUE_DIR / f"{date}.json"
    out.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out.relative_to(ROOT)}（{len(issue['news'])} 资讯 / {len(issue['repos'])} 项目）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
