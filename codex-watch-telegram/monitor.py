#!/usr/bin/env python3
"""
Codex Watch v2 -> Telegram

High-signal monitor for:
- Codex usage resets / extra usage / limits / credits / promotions
- pricing and plan changes
- model launches, retirements, deprecations, breaking changes
- useful Codex features
- stable Codex CLI releases
- Codex outages / recoveries
- important OpenAI collaborator comments on openai/codex
- free secondary-source relays for X/developer reset announcements

No OpenAI/ChatGPT/Codex API credits are used by this script.
"""

import hashlib
import html
import json
import os
import re
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
CONFIG_FILE = BASE_DIR / "config.json"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GH_TOKEN = os.environ.get("GH_TOKEN", "")

HTTP_HEADERS = {
    "User-Agent": "codex-watch-telegram/2.0",
}
GH_HEADERS = {
    **HTTP_HEADERS,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GH_TOKEN:
    GH_HEADERS["Authorization"] = f"Bearer {GH_TOKEN}"

DEFAULT_CONFIG = {
    "monitor": {
        "official_codex_changelog": True,
        "official_whats_new_digest": True,
        "official_usage_pricing_pages": True,
        "stable_github_releases": True,
        "codex_status": True,
        "developer_github_comments": True,
        "community_usage_relays": True,
        "security_plugin_updates": False,
    },
    "noise_control": {
        "alert_prereleases": False,
        "alert_bugfix_only_changelog_entries": False,
        "max_alerts_per_run": 12,
    },
}

OFFICIAL_CHANGELOG = "https://developers.openai.com/codex/changelog"
OFFICIAL_WHATS_NEW = "https://developers.openai.com/codex/whats-new"
SECURITY_CHANGELOG = "https://developers.openai.com/codex/security/plugin/changelog"

OFFICIAL_USAGE_PAGES = {
    "Using Codex with your ChatGPT plan":
        "https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan",
    "Codex rate card":
        "https://help.openai.com/en/articles/20001106-codex-rate-card",
    "Flexible usage credits":
        "https://help.openai.com/en/articles/12642688-using-credits-for-flexible-usage-in-chatgpt",
    "Codex referral promotions":
        "https://help.openai.com/en/articles/20001271-codex-referral-promotions",
}

STATUS_FEED = "https://status.openai.com/history.atom"
GITHUB_RELEASES_FEED = "https://github.com/openai/codex/releases.atom"

COMMUNITY_USAGE_FEEDS = {
    "r/codex usage/reset": (
        "https://www.reddit.com/r/codex/search.rss?"
        "q=reset%20OR%20usage%20OR%20limits%20OR%20credits%20OR%20quota%20OR%20free"
        "&restrict_sr=1&sort=new"
    ),
    "Google News — Codex usage/reset": (
        "https://news.google.com/rss/search?"
        "q=%28%22OpenAI+Codex%22+OR+%22Codex%22%29+"
        "%28%22usage+reset%22+OR+%22limits+reset%22+OR+%22usage+limit%22+"
        "OR+%22extra+usage%22+OR+%22free+usage%22+OR+credits+OR+quota%29"
        "&hl=en-US&gl=US&ceid=US:en"
    ),
    "Google News — Codex developer usage news": (
        "https://news.google.com/rss/search?"
        "q=%28%22Thibault+Sottiaux%22+OR+thsottiaux%29+"
        "%28Codex+reset+OR+Codex+usage+OR+Codex+limits%29"
        "&hl=en-US&gl=US&ceid=US:en"
    ),
}

GITHUB_HIGH_SIGNAL_SEARCHES = [
    "reset",
    '"usage limit"',
    '"extra usage"',
    "credits",
    "quota",
    "deprecated",
    "deprecation",
    "retire",
    "removed",
    '"breaking change"',
]

USAGE_TERMS = [
    "usage reset", "limits reset", "limit reset", "reset your usage",
    "reset usage", "reset limits", "free reset", "extra usage",
    "additional usage", "bonus usage", "free usage", "usage boost",
    "increased limits", "higher limits", "rate limit", "usage limit",
    "weekly limit", "5-hour limit", "five-hour limit", "quota",
    "credits", "credit", "promotion", "promotional", "referral",
]
ACTION_TERMS = [
    "retire", "retirement", "deprecated", "deprecation", "removed",
    "will no longer", "sunset", "breaking change", "migration required",
    "deadline", "discontinued",
]
PRICE_TERMS = [
    "price", "pricing", "rate card", "billing", "credit", "credits",
    "cost", "plan", "promotion", "promotional", "referral",
]
MODEL_TERMS = [
    "model", "gpt-", "reasoning effort", "model picker",
]
FEATURE_TERMS = [
    "new feature", "new features", "added ", "introducing", "now supports",
    "now available", "available in", "support for", "plugin", "plugins",
    "skill", "skills", "mcp", "automation", "computer use", "code review",
    "worktree", "agent", "terminal", "import", "desktop", "linux", "windows",
    "macos", "ide", "mobile", "browser", "remote", "cloud",
]
BUGFIX_TERMS = [
    "bug fix", "bug fixes", "performance improvements", "fixes",
]
SECURITY_TERMS = [
    "security", "vulnerability", "sandbox", "permission", "permissions",
    "secret", "secrets", "credential", "credentials",
]
CODEX_TERMS = [
    "codex", "@openai/codex", "coding agent",
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
PRERELEASE_RE = re.compile(r"(?:alpha|beta|rc\d*|nightly|preview|canary)", re.I)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            user_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for section, values in user_cfg.items():
                if isinstance(values, dict) and section in cfg:
                    cfg[section].update(values)
                else:
                    cfg[section] = values
        except Exception as exc:
            print(f"Config warning: {exc}")
    return cfg


def fresh_state():
    return {
        "schema": 2,
        "initialized": False,
        "seen": [],
        "alerted_versions": [],
        "weekly_digests": [],
        "usage_page_lines": {},
        "security_page_items": [],
        "source_baselines": {},
    }


def load_state():
    state = fresh_state()
    migrated_from_v1 = False

    if STATE_FILE.exists():
        try:
            old = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            old_schema = int(old.get("schema", 1))
            migrated_from_v1 = old_schema < 2 and bool(old.get("initialized", False))
            state.update(old)
        except Exception as exc:
            print(f"State warning: {exc}")

    state.setdefault("seen", [])
    state.setdefault("alerted_versions", [])
    state.setdefault("weekly_digests", [])
    state.setdefault("usage_page_lines", {})
    state.setdefault("security_page_items", [])
    state.setdefault("source_baselines", {})
    state["schema"] = 2
    state["_migrated_from_v1"] = migrated_from_v1
    return state


def save_state(state):
    state.pop("_migrated_from_v1", None)

    state["seen"] = state.get("seen", [])[-6000:]
    state["alerted_versions"] = state.get("alerted_versions", [])[-500:]
    state["weekly_digests"] = state.get("weekly_digests", [])[-100:]

    new_text = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    old_text = STATE_FILE.read_text(encoding="utf-8") if STATE_FILE.exists() else None

    if new_text == old_text:
        print("No state change; state.json left untouched.")
        return False

    STATE_FILE.write_text(new_text, encoding="utf-8")
    print("State changed.")
    return True


def source_is_baselining(state, source_name, global_baseline):
    return global_baseline or not state.setdefault("source_baselines", {}).get(source_name, False)


def mark_source_ready(state, source_name):
    state.setdefault("source_baselines", {})[source_name] = True


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def lower(text):
    return normalize(text).lower()


def contains_any(text, terms):
    t = lower(text)
    return any(term.lower() in t for term in terms)


def is_codex_related(text):
    return contains_any(text, CODEX_TERMS)


def is_usage_related(text):
    return contains_any(text, USAGE_TERMS)


def key_for(*parts):
    raw = "|".join(normalize(x) for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def remember(state, key):
    seen_list = state.setdefault("seen", [])
    if key in set(seen_list):
        return False
    seen_list.append(key)
    return True


def clean_html(value):
    return normalize(BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True))


def fetch(url, timeout=30):
    response = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def escape(value):
    return html.escape(str(value or ""), quote=False)


def classify(text, source_type="general"):
    t = lower(text)

    if source_type == "status":
        if any(x in t for x in ["resolved", "recovered", "fully operational"]):
            return ("✅ CODEX RECOVERED", 85, "If Codex was failing, the service should be recovering.")
        return ("🚨 CODEX OUTAGE", 100, "If Codex is failing, this may be service-side rather than your setup.")

    if any(x in t for x in [
        "usage reset", "limits reset", "limit reset", "reset your usage",
        "reset usage", "reset limits", "free reset", "extra usage",
        "additional usage", "bonus usage", "free usage", "usage boost",
        "increased limits", "higher limits",
    ]):
        return ("🚨 RESET / EXTRA USAGE", 110, "Worth checking Codex now; this may be temporary.")

    if contains_any(t, ACTION_TERMS):
        return ("🚨 ACTION NEEDED", 105, "Review this before any stated deadline or removal date.")

    if is_usage_related(t):
        return ("🟠 USAGE / LIMITS", 95, "This may affect how much Codex you can use.")

    if contains_any(t, PRICE_TERMS):
        return ("🟠 PRICING / CREDITS", 90, "This may affect your Codex cost, credits, or plan.")

    if contains_any(t, MODEL_TERMS):
        return ("🟠 MODEL UPDATE", 80, "Review this if you choose or pin models in Codex.")

    if contains_any(t, SECURITY_TERMS):
        return ("🟠 SECURITY", 75, "Useful if you rely on Codex permissions, sandboxing, or security features.")

    if source_type == "release":
        return ("🔵 CODEX RELEASE", 65, "Update if you want the new stable CLI release.")

    if contains_any(t, FEATURE_TERMS):
        return ("🔵 NEW FEATURE", 60, "No action required; this may improve your Codex workflow.")

    return ("🟢 CODEX NEWS", 40, "Useful Codex news; no immediate action required.")


class AlertQueue:
    def __init__(self, max_alerts):
        self.max_alerts = max(1, int(max_alerts))
        self.items = []

    def add(self, *, title, source, url, body="", author="", date="",
            source_type="general", force_badge=None, force_priority=None,
            action=None):
        badge, priority, default_action = classify(f"{title} {body}", source_type)
        self.items.append({
            "badge": force_badge or badge,
            "priority": force_priority if force_priority is not None else priority,
            "title": normalize(title)[:350],
            "source": source,
            "url": url,
            "body": normalize(body)[:1800],
            "author": normalize(author),
            "date": normalize(date),
            "action": action or default_action,
        })

    def _send(self, item):
        parts = [
            f"<b>{escape(item['badge'])}</b>",
            f"<b>{escape(item['title'])}</b>",
            "",
            f"<b>Why it matters:</b> {escape(item['action'])}",
            f"<b>Source:</b> {escape(item['source'])}",
        ]
        if item["author"]:
            parts.append(f"<b>By:</b> {escape(item['author'])}")
        if item["date"]:
            parts.append(f"<b>Date:</b> {escape(item['date'])}")
        if item["body"]:
            parts.extend(["", escape(item["body"][:1100])])
        if item["url"]:
            parts.extend(["", f'<a href="{html.escape(item["url"], quote=True)}">Open original source</a>'])
        telegram_send("\n".join(parts))

    def flush(self):
        if not self.items:
            print("No alerts to send.")
            return

        ordered = sorted(self.items, key=lambda x: x["priority"], reverse=True)
        selected = ordered[:self.max_alerts]

        for item in selected:
            self._send(item)

        suppressed = len(ordered) - len(selected)
        if suppressed > 0:
            telegram_send(
                "<b>ℹ️ CODEX WATCH</b>\n\n"
                f"{suppressed} lower-priority update(s) were suppressed this run "
                "to avoid notification spam."
            )


def telegram_send(text):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    response.raise_for_status()


def content_start_index(strings, marker):
    marker_l = marker.lower()
    for i, value in enumerate(strings):
        if marker_l in value.lower():
            return i + 1
    return 0


def changelog_date_blocks(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    strings = [normalize(x) for x in soup.stripped_strings if normalize(x)]
    start = content_start_index(strings, "ChatGPT & Codex changelog")
    strings = strings[start:]

    blocks = []
    i = 0
    while i < len(strings):
        if not DATE_RE.fullmatch(strings[i]):
            i += 1
            continue

        date = strings[i]
        j = i + 1
        chunk = []
        while j < len(strings) and not DATE_RE.fullmatch(strings[j]):
            chunk.append(strings[j])
            j += 1

        chunk = chunk[:180]

        title = ""
        for candidate in chunk:
            if candidate.lower() in {
                "view details", "new features", "bug fixes", "documentation",
                "chores", "changelog", "performance improvements and bug fixes",
            }:
                continue
            if len(candidate) >= 4:
                title = candidate
                break

        text = normalize(" ".join(chunk))
        if title and text:
            blocks.append({"date": date, "title": title, "text": text})

        i = j

    return blocks[:80]


def bugfix_only(block_text):
    t = lower(block_text)
    has_bugfix = contains_any(t, BUGFIX_TERMS)
    has_feature = contains_any(t, FEATURE_TERMS)
    has_important = (
        is_usage_related(t)
        or contains_any(t, ACTION_TERMS)
        or contains_any(t, PRICE_TERMS)
        or contains_any(t, MODEL_TERMS)
        or contains_any(t, SECURITY_TERMS)
    )
    return has_bugfix and not has_feature and not has_important


def check_official_changelog(state, config, queue, global_baseline):
    source_name = "official_changelog"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["official_codex_changelog"]:
        return

    try:
        html_text = fetch(OFFICIAL_CHANGELOG).text

        for block in changelog_date_blocks(html_text):
            text = f"{block['title']} {block['text']}"

            if not is_codex_related(text):
                continue

            if (
                not config["noise_control"]["alert_bugfix_only_changelog_entries"]
                and bugfix_only(text)
            ):
                continue

            item_key = key_for(
                "official-changelog", block["date"], block["title"], block["text"]
            )
            is_new = remember(state, item_key)

            version_match = VERSION_RE.search(block["title"])
            version = version_match.group(1) if version_match else None
            version_seen_before = bool(
                version and version in set(state.get("alerted_versions", []))
            )

            if version and not version_seen_before:
                state["alerted_versions"].append(version)

            if not is_new or baseline:
                continue

            # If GitHub already alerted this exact stable CLI version, do not
            # send the later changelog duplicate.
            if version_seen_before and "codex cli" in lower(block["title"]):
                continue

            queue.add(
                title=block["title"],
                source="Official ChatGPT & Codex changelog",
                url=OFFICIAL_CHANGELOG,
                body=block["text"][:1400],
                date=block["date"],
            )

        mark_source_ready(state, source_name)

    except Exception as exc:
        print(f"Official changelog error: {exc}")


def whats_new_sections(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    h1 = None
    for candidate in soup.find_all("h1"):
        if "what's new" in lower(candidate.get_text(" ", strip=True)):
            h1 = candidate
            break
    if not h1:
        return []

    sections = []
    for h2 in h1.find_all_next("h2"):
        heading = normalize(h2.get_text(" ", strip=True))
        if not re.search(r"\b20\d{2}\b", heading):
            continue

        topics = []
        node = h2.find_next()
        while node and node != h2:
            if getattr(node, "name", None) == "h2":
                break
            if getattr(node, "name", None) == "h3":
                topic = normalize(node.get_text(" ", strip=True))
                if topic and topic not in topics:
                    topics.append(topic)
            node = node.find_next()

        sections.append({"heading": heading, "topics": topics[:12]})

    return sections[:20]


def check_whats_new(state, config, queue, global_baseline):
    source_name = "whats_new"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["official_whats_new_digest"]:
        return

    try:
        html_text = fetch(OFFICIAL_WHATS_NEW).text
        known = state.setdefault("weekly_digests", [])
        known_set = set(known)

        for section in whats_new_sections(html_text):
            item_key = key_for("whats-new", section["heading"])
            if item_key in known_set:
                continue

            known.append(item_key)
            known_set.add(item_key)

            if baseline:
                continue

            body = (
                "Topics: " + "; ".join(section["topics"])
                if section["topics"] else ""
            )
            queue.add(
                title=f"What's new — {section['heading']}",
                source="Official Codex What's new digest",
                url=OFFICIAL_WHATS_NEW,
                body=body,
                force_badge="📰 WEEKLY CODEX DIGEST",
                force_priority=55,
                action="A concise official roundup of features worth knowing about.",
            )

        mark_source_ready(state, source_name)

    except Exception as exc:
        print(f"What's new error: {exc}")


def relevant_usage_lines(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines = []

    for node in root.find_all(["h1", "h2", "h3", "p", "li"]):
        text = normalize(node.get_text(" ", strip=True))
        if len(text) < 12:
            continue
        if (
            is_usage_related(text)
            or contains_any(text, PRICE_TERMS)
            or contains_any(text, ACTION_TERMS)
        ):
            text = text[:700]
            if text not in lines:
                lines.append(text)

    return lines[:100]


def check_usage_pages(state, config, queue, global_baseline):
    source_name = "usage_pages"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["official_usage_pricing_pages"]:
        return

    saved = state.setdefault("usage_page_lines", {})
    successful = 0

    for name, url in OFFICIAL_USAGE_PAGES.items():
        try:
            lines = relevant_usage_lines(fetch(url).text)
            if not lines:
                continue

            successful += 1
            old_lines = saved.get(name)
            saved[name] = lines

            if old_lines is None or baseline or lines == old_lines:
                continue

            old_set = set(old_lines)
            new_set = set(lines)
            additions = [x for x in lines if x not in old_set]
            removals = [x for x in old_lines if x not in new_set]

            details = []
            if additions:
                details.append(
                    "New/changed wording: " + " | ".join(additions[:3])
                )
            if removals and not additions:
                details.append(
                    "Relevant usage/pricing wording was removed or rewritten. "
                    "Review the official page."
                )

            queue.add(
                title=f"Official Codex usage/pricing page changed: {name}",
                source="OpenAI Help Center",
                url=url,
                body=" ".join(details)[:1500],
                force_priority=98,
                action="Review this because it may change Codex limits, credits, pricing, or promotions.",
            )

        except Exception as exc:
            print(f"Usage page error [{name}]: {exc}")

    if successful:
        mark_source_ready(state, source_name)


def check_status(state, config, queue, global_baseline):
    source_name = "status"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["codex_status"]:
        return

    try:
        feed = feedparser.parse(
            STATUS_FEED,
            request_headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
        )

        for entry in feed.entries[:40]:
            title = clean_html(entry.get("title", ""))
            summary = clean_html(
                entry.get("summary", "") or entry.get("description", "")
            )
            text = f"{title} {summary}"

            if "codex" not in lower(text):
                continue

            identifier = entry.get("id") or entry.get("link") or title
            updated = entry.get("updated", "") or entry.get("published", "")
            item_key = key_for("status", identifier, updated, title, summary)

            if not remember(state, item_key) or baseline:
                continue

            queue.add(
                title=title or "Codex status update",
                source="OpenAI Status",
                url=entry.get("link", "https://status.openai.com/"),
                body=summary,
                date=updated,
                source_type="status",
            )

        mark_source_ready(state, source_name)

    except Exception as exc:
        print(f"Status error: {exc}")


def stable_release(entry):
    title = clean_html(entry.get("title", ""))
    return not PRERELEASE_RE.search(title)


def check_github_releases(state, config, queue, global_baseline):
    source_name = "github_releases"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["stable_github_releases"]:
        return

    try:
        feed = feedparser.parse(
            GITHUB_RELEASES_FEED,
            request_headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
        )

        for entry in feed.entries[:30]:
            title = clean_html(entry.get("title", ""))
            summary = clean_html(
                entry.get("summary", "") or entry.get("description", "")
            )

            if (
                not config["noise_control"]["alert_prereleases"]
                and not stable_release(entry)
            ):
                continue

            version_match = VERSION_RE.search(title)
            version = version_match.group(1) if version_match else None

            identifier = entry.get("id") or entry.get("link") or title
            item_key = key_for("github-release", identifier)
            is_new = remember(state, item_key)

            # Changelog checker runs first. If it already recorded this exact
            # stable version, GitHub is a duplicate.
            version_seen_before = bool(
                version and version in set(state.get("alerted_versions", []))
            )

            if version and not version_seen_before:
                state["alerted_versions"].append(version)

            if not is_new or baseline or version_seen_before:
                continue

            queue.add(
                title=f"Codex CLI release {version}" if version else title,
                source="openai/codex GitHub releases",
                url=entry.get("link", "https://github.com/openai/codex/releases"),
                body=summary[:1300],
                date=entry.get("published", "") or entry.get("updated", ""),
                source_type="release",
            )

        mark_source_ready(state, source_name)

    except Exception as exc:
        print(f"GitHub releases error: {exc}")


def github_api(path, params=None):
    response = requests.get(
        "https://api.github.com" + path,
        headers=GH_HEADERS,
        params=params,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def check_developer_github_comments(state, config, queue, global_baseline):
    source_name = "developer_comments"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["developer_github_comments"]:
        return

    issues = {}
    search_success = False

    for term in GITHUB_HIGH_SIGNAL_SEARCHES:
        try:
            data = github_api(
                "/search/issues",
                {
                    "q": f"repo:openai/codex is:issue {term}",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": 8,
                },
            )
            search_success = True
            for issue in data.get("items", []):
                issues[issue["id"]] = issue
        except Exception as exc:
            print(f"GitHub search error [{term}]: {exc}")

    recent = sorted(
        issues.values(),
        key=lambda x: x.get("updated_at", ""),
        reverse=True,
    )[:12]

    for issue in recent:
        try:
            comments = github_api(
                f"/repos/openai/codex/issues/{issue['number']}/comments",
                {"per_page": 100},
            )

            for comment in comments[-100:]:
                association = (comment.get("author_association") or "").upper()
                if association not in {"MEMBER", "COLLABORATOR", "OWNER"}:
                    continue

                body = comment.get("body") or ""
                if not (
                    is_usage_related(body)
                    or contains_any(body, ACTION_TERMS)
                    or contains_any(body, PRICE_TERMS)
                ):
                    continue

                item_key = key_for("github-dev-comment", comment.get("id", ""))
                if not remember(state, item_key) or baseline:
                    continue

                queue.add(
                    title=f"OpenAI developer comment: {issue.get('title', 'Codex issue')}",
                    source="openai/codex GitHub",
                    url=comment.get("html_url", issue.get("html_url", "")),
                    body=body,
                    author=comment.get("user", {}).get("login", ""),
                    date=comment.get("created_at", ""),
                    force_priority=102 if is_usage_related(body) else 94,
                    action="High-signal comment from an OpenAI repository member/collaborator.",
                )

        except Exception as exc:
            print(f"GitHub comments error [#{issue.get('number')}]: {exc}")

    if search_success:
        mark_source_ready(state, source_name)


def check_community_usage_relays(state, config, queue, global_baseline):
    source_name = "community_usage"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["community_usage_relays"]:
        return

    successful = 0

    for source, url in COMMUNITY_USAGE_FEEDS.items():
        try:
            feed = feedparser.parse(
                url,
                request_headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
            )
            successful += 1

            for entry in feed.entries[:30]:
                title = clean_html(entry.get("title", ""))
                summary = clean_html(
                    entry.get("summary", "") or entry.get("description", "")
                )
                text = f"{title} {summary}"

                if not is_usage_related(text):
                    continue
                if "codex" not in lower(text):
                    continue

                identifier = entry.get("id") or entry.get("link") or title
                item_key = key_for("community-usage", source, identifier)

                if not remember(state, item_key) or baseline:
                    continue

                queue.add(
                    title=title or "Codex usage/reset report",
                    source=source,
                    url=entry.get("link", ""),
                    body=summary,
                    author=clean_html(entry.get("author", "")),
                    date=entry.get("published", "") or entry.get("updated", ""),
                    force_priority=88,
                    action="Unverified secondary report. Open the source and look for the original developer/OpenAI announcement.",
                )

        except Exception as exc:
            print(f"Community relay error [{source}]: {exc}")

    if successful:
        mark_source_ready(state, source_name)


def security_items(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    root = soup.find("main") or soup.body or soup
    items = []

    for h in root.find_all(["h2", "h3"]):
        title = normalize(h.get_text(" ", strip=True))
        if not title or title.lower() in {
            "changelog", "codex security plugin changelog"
        }:
            continue

        body_parts = []
        node = h.find_next_sibling()
        while node and getattr(node, "name", None) not in {"h2", "h3"}:
            text = normalize(node.get_text(" ", strip=True))
            if text:
                body_parts.append(text)
            node = node.find_next_sibling()

        body = normalize(" ".join(body_parts))
        if body:
            items.append((title, body[:1200]))

    return items[:40]


def check_security_plugin(state, config, queue, global_baseline):
    source_name = "security_plugin"
    baseline = source_is_baselining(state, source_name, global_baseline)

    if not config["monitor"]["security_plugin_updates"]:
        return

    try:
        items = security_items(fetch(SECURITY_CHANGELOG).text)
        known = state.setdefault("security_page_items", [])
        known_set = set(known)

        for title, body in items:
            item_key = key_for("security-plugin", title, body)
            if item_key in known_set:
                continue

            known.append(item_key)
            known_set.add(item_key)

            if baseline:
                continue

            queue.add(
                title=title,
                source="Official Codex Security plugin changelog",
                url=SECURITY_CHANGELOG,
                body=body,
                force_badge="🔐 CODEX SECURITY",
                force_priority=70,
            )

        mark_source_ready(state, source_name)

    except Exception as exc:
        print(f"Security plugin error: {exc}")


def main():
    config = load_config()
    state = load_state()

    fresh_install = not state.get("initialized", False)
    migrated_from_v1 = bool(state.get("_migrated_from_v1", False))
    global_baseline = fresh_install or migrated_from_v1

    queue = AlertQueue(config["noise_control"]["max_alerts_per_run"])

    # Official/high-signal sources first.
    check_official_changelog(state, config, queue, global_baseline)
    check_whats_new(state, config, queue, global_baseline)
    check_usage_pages(state, config, queue, global_baseline)
    check_status(state, config, queue, global_baseline)
    check_github_releases(state, config, queue, global_baseline)
    check_developer_github_comments(state, config, queue, global_baseline)

    # Free fallback for X/developer usage announcements.
    check_community_usage_relays(state, config, queue, global_baseline)

    # Optional; off by default.
    check_security_plugin(state, config, queue, global_baseline)

    if fresh_install:
        telegram_send(
            "<b>✅ CODEX WATCH v2 is active</b>\n\n"
            "Priority order:\n"
            "🚨 usage resets / extra usage / removals / deadlines\n"
            "🟠 limits / credits / pricing / models / security\n"
            "🔵 useful features / stable releases\n"
            "✅ outages and recoveries\n\n"
            "Community/news relays are used only for usage/reset signals. "
            "Broader Codex news comes from official OpenAI and openai/codex sources.\n\n"
            "The first run creates a baseline, so old news is not dumped into Telegram."
        )
        state["initialized"] = True

    elif migrated_from_v1:
        telegram_send(
            "<b>✅ CODEX WATCH upgraded to v2</b>\n\n"
            "Your existing history was preserved. New v2 sources were baselined "
            "silently, so you should not receive a flood of old Codex news."
        )

    else:
        queue.flush()

    save_state(state)


if __name__ == "__main__":
    main()
