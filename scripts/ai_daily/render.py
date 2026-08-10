#!/usr/bin/env python3
"""把当期稿件 JSON 渲染成 public/ai-daily.html。

用法:
    python3 scripts/ai_daily/render.py --latest
    python3 scripts/ai_daily/render.py --date 2026-08-07

稿件放在 data/issues/<date>.json，字段见 README_PIPELINE.md。
正文里可用 [[slug]] 或 [[slug|显示文字]] 标注术语，用 `反引号` 标注行内代码。
渲染器只会把正文真正用到的术语写进 window.__TERMS__，以满足 tests/rendered-html.test.mjs
中"页面嵌入的术语必须与渲染出的 data-slug 完全一致"这条断言。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHELL = Path(__file__).parent / "shell.html"
TERMS_DB = Path(__file__).parent / "terms.json"
ISSUE_DIR = ROOT / "data" / "issues"
OUTPUT = ROOT / "public" / "ai-daily.html"

CST = timezone(timedelta(hours=8))
NOTE_ORDER = ["这是", "不同", "适合", "今天看", "注意"]

TERM_RE = re.compile(r"\[\[([a-z0-9\-]+)(?:\|([^\]]+))?\]\]")
CODE_RE = re.compile(r"`([^`]+)`")


class RenderError(Exception):
    pass


def rich(text: str, terms_db: dict, used: set) -> str:
    """转义文本，并把 [[slug]] 与 `code` 转成 HTML。"""
    out = html.escape(str(text), quote=False)

    def code_sub(m):
        return f"<code>{m.group(1)}</code>"

    def term_sub(m):
        slug, label = m.group(1), m.group(2)
        if slug not in terms_db:
            raise RenderError(
                f"正文引用了未知术语 slug '{slug}'，请先在 scripts/ai_daily/terms.json 中定义"
            )
        entry = terms_db[slug]
        label = label or entry.get("display") or entry.get("title") or slug
        used.add(slug)
        kind = "wiki" if entry.get("kind") == "wiki" else "temp"
        return (
            f'<button class="term term--{kind}" data-slug="{slug}" '
            f'type="button">{label}</button>'
        )

    # 先处理术语（其内容已转义），再处理行内代码
    out = TERM_RE.sub(term_sub, out)
    out = CODE_RE.sub(code_sub, out)
    return out


def attr(text: str) -> str:
    return html.escape(str(text), quote=True)


def render_news(news: list, terms_db: dict, used: set) -> str:
    if not news:
        raise RenderError("news 不能为空")
    blocks = []
    for i, item in enumerate(news, 1):
        for key in ("title", "url", "why"):
            if not item.get(key):
                raise RenderError(f"第 {i} 条资讯缺少必填字段 '{key}'")
        paras = item.get("paras") or []
        if not paras:
            raise RenderError(f"第 {i} 条资讯缺少正文段落 paras")
        body = "\n".join(f"<p>{rich(p, terms_db, used)}</p>" for p in paras)
        blocks.append(
            f'<article id="item-{i}">\n'
            f'<div class="item-top"><span class="idx" aria-hidden="true">{i:02d}</span>'
            f'<span class="from">{rich(item.get("source", ""), terms_db, used)}</span></div>\n'
            f'<h2>{rich(item["title"], terms_db, used)}</h2>\n'
            f'<div class="body">\n{body}\n'
            f'<p class="why"><strong>为什么值得关心</strong>：'
            f'{rich(item["why"], terms_db, used)}</p>\n'
            f'</div>\n'
            f'<p class="readmore"><a href="{attr(item["url"])}" target="_blank" '
            f'rel="noopener noreferrer">读原文 ↗</a></p>\n'
            f'</article>'
        )
    return '<div class="news-grid">\n' + "\n".join(blocks) + "\n</div>"


def render_repos(repos: list, terms_db: dict, used: set) -> str:
    if not repos:
        raise RenderError("repos 不能为空，测试要求页面含 repo-card")
    cards = []
    for repo in repos:
        for key in ("name", "url"):
            if not repo.get(key):
                raise RenderError(f"GitHub 项目缺少必填字段 '{key}'：{repo}")
        if not repo["url"].startswith("https://github.com/"):
            raise RenderError(f"项目链接必须是 github.com 地址：{repo['url']}")

        metrics = "\n".join(
            f"<span>{label} <b>{rich(repo[key], terms_db, used)}</b></span>"
            for label, key in (
                ("Stars", "stars"),
                ("增长", "growth"),
                ("语言", "lang"),
                ("许可", "license"),
            )
            if repo.get(key)
        )
        notes_src = repo.get("notes") or {}
        notes = []
        for key in NOTE_ORDER:
            if not notes_src.get(key):
                continue
            cls = "repo-note is-diff" if key == "不同" else "repo-note"
            notes.append(
                f'<div class="{cls}"><span class="k">{key}</span>'
                f'<span class="v">{rich(notes_src[key], terms_db, used)}</span></div>'
            )
        if not notes:
            raise RenderError(f"项目 {repo['name']} 缺少解读 notes")

        cards.append(
            '<article class="repo-card">\n'
            '<div class="repo-visual"><div class="repo-head">\n'
            f'<h3><a href="{attr(repo["url"])}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(repo["name"])}</a></h3>\n'
            f'<span class="repo-category">'
            f'{rich(repo.get("category", ""), terms_db, used)}</span></div></div>\n'
            '<div class="repo-body">\n'
            f'<div class="repo-metrics">\n{metrics}\n</div>\n'
            f'<div class="repo-notes">\n' + "\n".join(notes) + "\n</div>\n"
            f'<p class="repo-link"><a href="{attr(repo["url"])}" target="_blank" '
            f'rel="noopener noreferrer">查看 GitHub 仓库 ↗</a></p>\n'
            '</div></article>'
        )

    return (
        '<section class="github-radar" id="github">\n'
        '<p class="section-kicker">Open-source radar</p>\n'
        '<h2 class="serif">GitHub 今日高星项目</h2>\n'
        '<p class="section-deck">不只看总星数：同时看增长势头、活跃度、项目定位和与同类的真实差异。</p>\n'
        '<div class="repo-grid">\n' + "\n".join(cards) + "\n</div></section>"
    )


def render_footer(pending: dict) -> str:
    chips = "\n".join(
        f"<span>{html.escape(str(word))} <b>×{int(count)}</b></span>"
        for word, count in (pending or {}).items()
    )
    return (
        "<footer>\n"
        "<h3>怎么读这些标注</h3>\n"
        '<div class="legend"><span><i></i>这个词已在知识库中收录 —— 点开查看简明解释</span>'
        '<span><i class="l-prov"></i>临时解释 —— 本期现写的，还没进知识库</span></div>\n'
        "<h3>待摄入队列 · 出现越多越值得正式收进去</h3>\n"
        f'<div class="pending">\n{chips}\n</div>\n'
        '<p class="note">日报术语来自维护中的知识库，仅用于解释本期内容。</p>\n'
        "</footer>"
    )


def build(issue: dict) -> str:
    terms_db = json.loads(TERMS_DB.read_text(encoding="utf-8"))
    # 当期临时术语可直接写在稿件里，不污染长期知识库
    for slug, entry in (issue.get("terms") or {}).items():
        entry.setdefault("kind", "temp")
        terms_db[slug] = entry

    used: set = set()
    news_html = render_news(issue.get("news") or [], terms_db, used)
    repos_html = render_repos(issue.get("repos") or [], terms_db, used)

    news_count = len(issue["news"])
    repo_count = len(issue["repos"])
    updated = issue.get("updated") or datetime.now(CST).strftime("%m-%d %H:%M")

    header = (
        '<header class="masthead">\n'
        f'<div class="brandline"><span class="brand">AI 日报</span>'
        f'<span class="stamp">{html.escape(issue.get("range", issue["date"]))}</span></div>\n'
        f'<h1 class="serif">{html.escape(issue["headline"])}</h1>\n'
        f'<div class="stats"><span><b>{news_count}</b> 条</span>'
        f'<span>GitHub 项目 <b>{repo_count}</b> 个</span>'
        f'<span>知识库术语 <b>{len(used)}</b> 个</span>'
        f'<span>更新于 {html.escape(updated)}</span></div>\n'
        "</header>"
    )

    content = (
        '<div class="wrap">\n'
        + header + "\n"
        + news_html + "\n"
        + repos_html + "\n"
        + render_footer(issue.get("pending"))
        + "\n</div>"
    )

    # 只嵌入正文实际渲染出的术语，与 data-slug 严格一致
    payload = {slug: terms_db[slug] for slug in sorted(used)}
    shell = SHELL.read_text(encoding="utf-8")
    return shell.replace("__CONTENT__", content).replace(
        "__TERMS_JSON__", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def verify(page: str) -> None:
    """本地自检，等价于 tests/rendered-html.test.mjs 的核心断言。"""
    payload = re.search(r"<script>window\.__TERMS__=(\{.*?\});</script>", page, re.S)
    if not payload:
        raise RenderError("生成的页面缺少 __TERMS__ 负载")
    embedded = sorted(json.loads(payload.group(1)))
    rendered = sorted(set(re.findall(r'data-slug="([^"]+)"', page)))
    if embedded != rendered:
        raise RenderError(f"术语不匹配：嵌入 {embedded}，渲染 {rendered}")
    for needle in ("AI 日报", "GitHub 今日高星项目", 'class="repo-card"'):
        if needle not in page:
            raise RenderError(f"页面缺少必需内容：{needle}")
    if not re.search(r"https://github\.com/[^\"<]+", page):
        raise RenderError("页面缺少 GitHub 链接")


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染 AI 日报页面")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="指定期号日期 YYYY-MM-DD")
    group.add_argument("--latest", action="store_true", help="使用最新一期稿件")
    ap.add_argument("--dry-run", action="store_true", help="只校验不写文件")
    args = ap.parse_args()

    if args.latest:
        issues = sorted(ISSUE_DIR.glob("*.json"))
        if not issues:
            print(f"找不到任何稿件，请先在 {ISSUE_DIR} 放入 <date>.json", file=sys.stderr)
            return 1
        path = issues[-1]
    else:
        path = ISSUE_DIR / f"{args.date}.json"
        if not path.exists():
            print(f"稿件不存在：{path}", file=sys.stderr)
            return 1

    issue = json.loads(path.read_text(encoding="utf-8"))
    issue.setdefault("date", path.stem)
    try:
        page = build(issue)
        verify(page)
    except RenderError as exc:
        print(f"渲染失败：{exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"校验通过（未写入），共 {len(page)} 字符")
        return 0

    OUTPUT.write_text(page, encoding="utf-8")
    print(f"已生成 {OUTPUT.relative_to(ROOT)}（{path.stem} 期，{len(page)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
