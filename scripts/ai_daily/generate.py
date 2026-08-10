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

SYSTEM_PROMPT = """你是一位服务「AI 产品经理」的中文科技简报作者。读者是看不懂英文的 AI 产品负责人，他们只关心三件事：这件事是什么、为什么和我（做 AI 产品的）有关、我该据此调整什么判断。

【语言：全中文】
- 全部用中文撰写。素材里的英文标题、产品名、技术名词必须翻译成中文或给出中文译名；专有名词（如 GitHub、MCP、Agent）可保留英文并括注中文，但绝不在正文留大段英文。
- 资讯标题 title 用中文概括，不要直接照抄英文原文。
- 来源 source 用中文可读写法，例如「Hacker News 热帖 · 839 分 / 906 评」「GitHub 热门项目」「官方博客」。

【视角：为什么 AI 产品经理需要关注】
- 每条资讯的 why 段，必须从「AI 产品经理」视角讲清意义：它对产品策略、用户价值、竞品格局、落地成本或风险有什么具体影响。
- 不要泛泛写「很重要」「值得关注」，要落到可操作的产品判断上（例如：这会不会改变我们的路线 / 某个功能该怎么定 / 对标竞品在做什么）。

【写法：只要有用的信息，不罗列】
- 写成连贯的中文段落（paras），像写给同事看的一页简报。禁止使用项目符号、编号或功能点清单来堆砌。
- 每条资讯只讲一件事，2 段左右：第 1 段说清「发生了什么、关键事实是什么」；第 2 段讲「对 AI 产品经理的具体含义」。
- 只保留真正有用的信息，砍掉凑数内容和营销话术；不为了凑数而罗列。

【格式与术语】
- 可在正文用 [[slug]] 标注术语、用 `反引号` 标注行内代码（如 `MCP`）。
- 若某术语不在下方白名单且你要用，必须在 terms 里补一条 kind:temp 解释。
- 不要编造无法从素材确认的数字、日期、人名、引语；拿不准就措辞谨慎。

严格输出如下 JSON（不要任何额外文字、不要 markdown 代码块）：
{
  "headline": "一句话本期主题（30 字内，中文）",
  "news": [
    {
      "source": "中文来源说明，如 'Hacker News 热帖 · 839 分 / 906 评' 或 'Google 官方博客 / Hacker News'",
      "title": "中文标题",
      "url": "原文链接",
      "paras": ["第 1 段：发生了什么（中文，连贯段落，无罗列）", "第 2 段：对 AI 产品经理的具体含义（中文，连贯段落）"],
      "why": "为什么 AI 产品经理需要关注：落到产品策略 / 用户价值 / 竞品 / 风险的具体判断"
    }
  ],
  "repos": [
    {
      "name": "owner/repo（保留英文原名，作为链接）",
      "url": "https://github.com/owner/repo",
      "category": "一句话中文归类",
      "stars": "如 '238,477'（未知写 '—'）",
      "growth": "增量说明或 '—'",
      "lang": "主要语言或 '—'",
      "license": "许可证或 '—'",
      "notes": {
        "这是": "中文：它是什么",
        "不同": "中文：与同类最关键的差异（会高亮）",
        "适合": "中文：适合什么场景 / 什么团队",
        "今天看": "中文：为什么这期值得 AI 产品经理关注",
        "注意": "中文：风险或前提"
      }
    }
  ],
  "terms": { "slug": {"display":"显示名","title":"标题","kind":"temp","plain":"简明中文解释","why":"为什么提它"} },
  "pending": {"出现多次值得收进知识库的词": 3}
}
约束：news 5~8 条；repos 3~4 个；每条 news 必须有 title(中文)/url/why(从产品经理视角)/paras(非空中文，非罗列)；每条 repo 必须有 name/url(https://github.com/ 开头)/notes（5 项至少填一项，全中文）。date 与 range 由系统填写，你无需输出。"""


def _extract_json(text: str) -> dict | None:
    """从可能夹杂思考过程/代码的文本里，按括号配平取出最后一个完整 JSON 对象。"""
    candidates = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
                    start = None
    # 优先尝试最后的完整对象（推理模型的答案通常在末尾）
    for c in reversed(candidates):
        try:
            return json.loads(c)
        except Exception:  # noqa: BLE001
            continue
    return None


def call_llm(api_key: str, base_url: str, model: str, user_content: str) -> dict | None:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens": 16384,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=240, context=ssl.create_default_context()) as resp:
            raw = resp.read()
        payload = json.loads(raw)
        msg = payload["choices"][0]["message"]
        # 兼容推理模型：最终答案可能在 content，也可能在 reasoning_content
        text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        # 去掉可能的 ```json 围栏
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
        return _extract_json(text)
    except urllib.error.HTTPError as exc:  # noqa: BLE001
        b = exc.read().decode("utf-8", "ignore")[:600]
        print(f"  [LLM HTTP 错误 {exc.code}] {b}", file=sys.stderr)
        return None
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
注意：上方素材标题多为英文，撰写时请全部翻译为中文，正文不要保留大段英文。
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
    print("  [降级] 使用简单聚合（无大模型解读，内容未经翻译与产品经理视角加工）", file=sys.stderr)
    news = []
    for h in raw.get("hackernews", [])[:8]:
        news.append({
            "source": f"Hacker News 热帖 · {h['points']}分 / {h['comments']}评",
            "title": h["title"],
            "url": h["url"],
            "paras": ["（当前为无大模型连接的降级模式，以下为原始标题，未做中文翻译与解读，请补充。）", h["title"]],
            "why": "（自动聚合，未从 AI 产品经理视角解读，请补充。）",
        })
    repos = []
    for g in raw.get("github", [])[:4]:
        repos.append({
            "name": g["name"], "url": g["url"],
            "category": "GitHub 热门项目", "stars": str(g["stars"]),
            "growth": "—", "lang": g.get("lang") or "—",
            "license": g.get("license") or "—",
            "notes": {"这是": g.get("description") or "—", "今天看": "本期自动聚合入选，待人工补充中文解读。"},
        })
    return {
        "headline": "AI 日报 · 今日速览（自动聚合，未做中文解读）",
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
