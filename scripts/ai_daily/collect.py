#!/usr/bin/env python3
"""采集当日 AI 资讯与 GitHub 项目的原始素材。

用法:
    python3 scripts/ai_daily/collect.py            # 采集近 2 天
    python3 scripts/ai_daily/collect.py --days 3

产物写入 data/raw/<date>.json，供撰稿环节挑选。
只依赖标准库；设置环境变量 GITHUB_TOKEN 可提升 GitHub API 速率上限。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
STATE = ROOT / "data" / "state" / "repo_stars.json"

CST = timezone(timedelta(hours=8))
UA = "ai-daily-radar/1.0 (+https://github.com/fenlili0108-source/ai-daily-radar)"

# Hacker News 检索关键词
HN_QUERIES = [
    "AI", "LLM", "GPT", "agent", "OpenAI", "Anthropic",
    "machine learning", "transformer", "inference", "open source model",
]

# GitHub 检索维度
GH_QUERIES = [
    "topic:llm", "topic:ai-agent", "topic:artificial-intelligence",
    "topic:machine-learning", "topic:generative-ai",
]

RSS_FEEDS = [
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("Google AI", "https://blog.google/technology/ai/rss/"),
    ("BAIR", "https://bair.berkeley.edu/blog/feed.xml"),
    ("MIT News AI", "https://news.mit.edu/rss/topic/artificial-intelligence2"),
]

# 明显与 AI 无关的高分帖过滤词
NOISE = re.compile(r"\b(ask hn|show hn: my|hiring|who is hiring)\b", re.I)


def fetch(url: str, headers: dict | None = None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"  [跳过] {url} -> {exc}", file=sys.stderr)
        return b""


def fetch_json(url: str, headers: dict | None = None) -> dict:
    raw = fetch(url, headers)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [跳过] {url} 返回的不是合法 JSON", file=sys.stderr)
        return {}


def collect_hn(days: int) -> list:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    seen, items = set(), []
    for query in HN_QUERIES:
        params = urllib.parse.urlencode({
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff},points>40",
            "hitsPerPage": 30,
        })
        data = fetch_json(f"https://hn.algolia.com/api/v1/search?{params}")
        for hit in data.get("hits", []):
            oid = hit.get("objectID")
            title = (hit.get("title") or "").strip()
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            if not title or oid in seen or NOISE.search(title):
                continue
            seen.add(oid)
            items.append({
                "title": title,
                "url": url,
                "points": hit.get("points", 0),
                "comments": hit.get("num_comments", 0),
                "author": hit.get("author"),
                "created_at": hit.get("created_at"),
                "hn_url": f"https://news.ycombinator.com/item?id={oid}",
            })
        time.sleep(0.4)
    items.sort(key=lambda x: x["points"], reverse=True)
    return items[:40]


def collect_github(days: int) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    prev = {}
    if STATE.exists():
        prev = json.loads(STATE.read_text(encoding="utf-8"))

    seen, repos = set(), []
    for q in GH_QUERIES:
        params = urllib.parse.urlencode({
            "q": f"{q} pushed:>{since} stars:>800",
            "sort": "stars",
            "order": "desc",
            "per_page": 20,
        })
        data = fetch_json(
            f"https://api.github.com/search/repositories?{params}", headers
        )
        for repo in data.get("items", []):
            name = repo.get("full_name")
            if not name or name in seen:
                continue
            seen.add(name)
            stars = repo.get("stargazers_count", 0)
            before = prev.get(name, {}).get("stars")
            repos.append({
                "name": name,
                "url": repo.get("html_url"),
                "description": (repo.get("description") or "").strip(),
                "stars": stars,
                "delta": (stars - before) if isinstance(before, int) else None,
                "lang": repo.get("language"),
                "license": (repo.get("license") or {}).get("spdx_id"),
                "topics": repo.get("topics", [])[:8],
                "pushed_at": repo.get("pushed_at"),
                "homepage": repo.get("homepage"),
            })
        time.sleep(2.5)  # 未认证检索接口限速 10 次/分钟

    # 有增量的排前面，便于挑出"势头"项目
    repos.sort(key=lambda r: (r["delta"] is None, -(r["delta"] or 0), -r["stars"]))

    STATE.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        r["name"]: {"stars": r["stars"], "date": datetime.now(CST).strftime("%Y-%m-%d")}
        for r in repos
    }
    STATE.write_text(
        json.dumps({**prev, **snapshot}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return repos[:30]


def strip_tags(text: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def collect_rss(days: int) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)
    out = []
    for source, url in RSS_FEEDS:
        raw = fetch(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            print(f"  [跳过] {source} RSS 解析失败", file=sys.stderr)
            continue

        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//a:entry", ns)
        for entry in entries[:15]:
            def pick(*tags):
                for tag in tags:
                    node = entry.find(tag) if not tag.startswith("a:") else entry.find(tag, ns)
                    if node is not None:
                        return (node.text or "").strip() or (node.get("href") or "")
                return ""

            title = pick("title", "a:title")
            link = pick("link", "a:link")
            if not link:
                node = entry.find("a:link", ns)
                link = node.get("href") if node is not None else ""
            published = pick("pubDate", "a:updated", "a:published")

            keep = True
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    dt = datetime.strptime(published, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    keep = dt >= cutoff
                    break
                except (ValueError, TypeError):
                    continue
            if not (title and link and keep):
                continue
            out.append({
                "source": source,
                "title": title,
                "url": link,
                "published": published,
                "summary": strip_tags(pick("description", "a:summary", "a:content")),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="采集 AI 日报原始素材")
    ap.add_argument("--days", type=int, default=2, help="回溯天数，默认 2")
    ap.add_argument("--date", help="输出期号，默认今天")
    args = ap.parse_args()

    date = args.date or datetime.now(CST).strftime("%Y-%m-%d")
    print(f"采集 {date} 期素材（回溯 {args.days} 天）")

    print("· Hacker News …")
    hn = collect_hn(args.days)
    print(f"  得到 {len(hn)} 条")

    print("· GitHub …")
    gh = collect_github(args.days)
    print(f"  得到 {len(gh)} 个仓库")

    print("· RSS …")
    rss = collect_rss(args.days)
    print(f"  得到 {len(rss)} 条")

    payload = {
        "date": date,
        "collected_at": datetime.now(CST).isoformat(timespec="seconds"),
        "days": args.days,
        "hackernews": hn,
        "github": gh,
        "rss": rss,
    }
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"{date}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {out.relative_to(ROOT)}")

    if not hn and not rss:
        print("警告：资讯源全部为空，可能是网络问题", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
