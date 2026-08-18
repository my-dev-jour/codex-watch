#!/usr/bin/env python3
import os
import re
import json
import hashlib
import html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GH_TOKEN = os.environ.get("GH_TOKEN", "")

HEADERS = {
    "User-Agent": "codex-watch-telegram/1.0",
    "Accept": "application/vnd.github+json",
}
if GH_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GH_TOKEN}"

# Usage/reset is deliberately weighted much higher than ordinary Codex news.
URGENT_TERMS = [
    "usage reset", "limits reset", "limit reset", "reset your usage",
    "reset usage", "reset limits", "free reset", "extra usage",
    "additional usage", "bonus usage", "free usage", "usage boost",
    "increased limits", "higher limits", "rate limit", "usage limit",
    "weekly limit", "5-hour limit", "credits", "credit", "quota",
]
CODEX_TERMS = ["codex", "gpt-5", "coding agent"]

OFFICIAL_PAGES = {
    "OpenAI Codex changelog": "https://developers.openai.com/codex/changelog",
    "OpenAI release notes": "https://openai.com/products/release-notes/",
}

RSS_SOURCES = {
    "OpenAI Status": "https://status.openai.com/history.atom",
    "GitHub Codex releases": "https://github.com/openai/codex/releases.atom",
    "r/codex usage/reset": (
        "https://www.reddit.com/r/codex/search.rss?"
        "q=reset%20OR%20usage%20OR%20limits%20OR%20credits%20OR%20quota"
        "&restrict_sr=1&sort=new"
    ),
    "Google News — Codex reset/usage": (
        "https://news.google.com/rss/search?"
        "q=%28%22OpenAI+Codex%22+OR+Codex%29+"
        "%28%22usage+reset%22+OR+%22limits+reset%22+OR+%22usage+limit%22+"
        "OR+%22extra+usage%22+OR+credits+OR+quota%29"
        "&hl=en-US&gl=US&ceid=US:en"
    ),
    "Google News — Thibault/Codex": (
        "https://news.google.com/rss/search?"
        "q=%28%22Thibault+Sottiaux%22+OR+thsottiaux%29+Codex"
        "&hl=en-US&gl=US&ceid=US:en"
    ),
}

GITHUB_SEARCH_TERMS = [
    "reset",
    '"usage limit"',
    '"extra usage"',
    "credits",
    "quota",
]


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "initialized": False,
        "seen": [],
        "page_snippets": {},
    }


def save_state(state):
    # Bound state growth.
    state["seen"] = state.get("seen", [])[-4000:]
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def clean_text(value):
    value = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def relevant(text, require_codex=False):
    t = text.lower()
    urgent = any(term in t for term in URGENT_TERMS)
    if require_codex:
        return urgent and any(term in t for term in CODEX_TERMS)
    return urgent


def classify(text):
    t = text.lower()
    if any(x in t for x in [
        "usage reset", "limits reset", "limit reset", "reset your usage",
        "reset usage", "reset limits", "free reset", "extra usage",
        "additional usage", "bonus usage", "free usage", "usage boost",
        "increased limits", "higher limits"
    ]):
        return "🚨 RESET / EXTRA USAGE"
    if any(x in t for x in ["usage limit", "weekly limit", "5-hour limit", "rate limit", "quota"]):
        return "🟠 USAGE LIMIT"
    if "credit" in t:
        return "🟠 CREDITS"
    return "🔵 CODEX USAGE"


def esc(s):
    return html.escape(str(s), quote=False)


def telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    r.raise_for_status()


def item_key(source, identifier):
    return hashlib.sha256(f"{source}|{identifier}".encode()).hexdigest()


def alert(source, title, url, body="", author="", date=""):
    combined = f"{title} {body}"
    badge = classify(combined)
    parts = [
        f"<b>{esc(badge)}</b>",
        f"<b>{esc(title)}</b>",
        "",
        f"<b>Source:</b> {esc(source)}",
    ]
    if author:
        parts.append(f"<b>By:</b> {esc(author)}")
    if date:
        parts.append(f"<b>Date:</b> {esc(date)}")
    snippet = clean_text(body)
    if snippet:
        parts.extend(["", esc(snippet[:900])])
    parts.extend(["", f'<a href="{html.escape(url, quote=True)}">Open original source</a>'])
    telegram("\n".join(parts))


def check_rss(state, baseline=False):
    seen = set(state["seen"])
    for source, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": HEADERS["User-Agent"]})
            for entry in feed.entries[:30]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
                link = entry.get("link", "")
                text = f"{title} {summary}"
                # Status feed must explicitly mention Codex; other feeds are already scoped.
                if source == "OpenAI Status":
                    if "codex" not in text.lower():
                        continue
                elif source == "GitHub Codex releases":
                    # Releases are not the main purpose; only alert if usage/reset related.
                    if not relevant(text):
                        continue
                elif not relevant(text):
                    continue

                key = item_key(source, entry.get("id") or link or title)
                if key in seen:
                    continue
                seen.add(key)
                if not baseline:
                    alert(
                        source=source,
                        title=title or "Codex usage update",
                        url=link,
                        body=summary,
                        author=clean_text(entry.get("author", "")),
                        date=clean_text(entry.get("published", "") or entry.get("updated", "")),
                    )
        except Exception as e:
            print(f"RSS error [{source}]: {e}")
    state["seen"] = list(seen)


def github_api(path, params=None):
    r = requests.get(
        "https://api.github.com" + path,
        headers=HEADERS,
        params=params,
        timeout=25,
    )
    r.raise_for_status()
    return r.json()


def check_github_issues_and_dev_comments(state, baseline=False):
    seen = set(state["seen"])
    issues = {}

    for term in GITHUB_SEARCH_TERMS:
        try:
            data = github_api(
                "/search/issues",
                {
                    "q": f"repo:openai/codex is:issue {term}",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": 10,
                },
            )
            for issue in data.get("items", []):
                issues[issue["id"]] = issue
        except Exception as e:
            print(f"GitHub search error [{term}]: {e}")

    # New relevant issues/posts.
    for issue in sorted(issues.values(), key=lambda x: x.get("updated_at", ""), reverse=True)[:25]:
        title = issue.get("title", "")
        body = issue.get("body") or ""
        if not relevant(f"{title} {body}"):
            continue

        key = item_key("github-issue", issue["id"])
        if key not in seen:
            seen.add(key)
            if not baseline:
                alert(
                    source="openai/codex GitHub issue",
                    title=title,
                    url=issue.get("html_url", ""),
                    body=body,
                    author=issue.get("user", {}).get("login", ""),
                    date=issue.get("updated_at", ""),
                )

    # Most useful part for your use case:
    # scan recent matching issues for NEW comments from OpenAI repo members/collaborators.
    for issue in sorted(issues.values(), key=lambda x: x.get("updated_at", ""), reverse=True)[:10]:
        try:
            comments = github_api(
                f"/repos/openai/codex/issues/{issue['number']}/comments",
                {"per_page": 100, "sort": "created", "direction": "desc"},
            )
            for comment in comments[-100:]:
                assoc = (comment.get("author_association") or "").upper()
                if assoc not in {"MEMBER", "COLLABORATOR", "OWNER"}:
                    continue
                body = comment.get("body") or ""
                if not relevant(body):
                    continue

                key = item_key("github-comment", comment["id"])
                if key in seen:
                    continue
                seen.add(key)
                if not baseline:
                    alert(
                        source="OpenAI collaborator comment on openai/codex",
                        title=f"Developer comment: {issue.get('title', 'Codex issue')}",
                        url=comment.get("html_url", issue.get("html_url", "")),
                        body=body,
                        author=comment.get("user", {}).get("login", ""),
                        date=comment.get("created_at", ""),
                    )
        except Exception as e:
            print(f"GitHub comments error [#{issue.get('number')}]: {e}")

    state["seen"] = list(seen)


def extract_usage_snippets(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    candidates = []
    for node in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = clean_text(node.get_text(" ", strip=True))
        if len(text) < 20:
            continue
        if relevant(text):
            candidates.append(text)
    # Keep unique snippets, preserving order.
    out = []
    seen = set()
    for x in candidates:
        normalized = x.lower()
        if normalized not in seen:
            seen.add(normalized)
            out.append(x)
    return out[:100]


def check_official_pages(state, baseline=False):
    old = state.setdefault("page_snippets", {})
    for source, url in OFFICIAL_PAGES.items():
        try:
            r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30)
            r.raise_for_status()
            snippets = extract_usage_snippets(r.text)
            previous = set(old.get(source, []))
            current_hashes = [hashlib.sha256(x.encode()).hexdigest() for x in snippets]
            prev_hashes = set(previous)

            for snippet, h in zip(snippets, current_hashes):
                if h not in prev_hashes and not baseline:
                    alert(
                        source=source,
                        title="Official Codex usage/limit wording changed",
                        url=url,
                        body=snippet,
                    )
            old[source] = current_hashes
        except Exception as e:
            print(f"Page error [{source}]: {e}")


def main():
    state = load_state()
    baseline = not state.get("initialized", False)

    check_rss(state, baseline=baseline)
    check_github_issues_and_dev_comments(state, baseline=baseline)
    check_official_pages(state, baseline=baseline)

    if baseline:
        telegram(
            "<b>✅ CODEX WATCH is active</b>\n\n"
            "I will alert this Telegram chat when I detect Codex usage resets, "
            "extra/free usage, limit changes, credits/quota changes, relevant "
            "OpenAI collaborator comments on GitHub, or Codex-related status incidents.\n\n"
            "The first run creates a baseline, so it does not flood you with old posts."
        )
        state["initialized"] = True

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
