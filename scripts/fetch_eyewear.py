"""
fetch_eyewear.py
Fetches the latest headlines from Eyewear Intelligence (ewintelligence.com)
and returns only the articles published since the last run.

State is tracked by article ID (the numeric id in each /*.article URL,
which increases monotonically) rather than by date, since IDs are more
robust to any timezone/formatting drift on the site.

State file (JSON): {"last_seen_id": <int>}
"""

import json
import logging
import os
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADLINES_URL = "https://www.ewintelligence.com/latest-headlines/1603.more?navcode=358"
ARTICLE_ID_RE = re.compile(r"/(\d+)\.article$")


def _load_state(state_path: str) -> dict:
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state file %s: %s", state_path, exc)
    return {"last_seen_id": 0}


def _save_state(state_path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for li in soup.select("div.listBlocks li"):
        h3_a = li.select_one("h3 a")
        if not h3_a or not h3_a.get("href"):
            continue
        url = h3_a["href"]
        m = ARTICLE_ID_RE.search(url)
        if not m:
            continue
        date_span = li.select_one("span.date")
        articles.append({
            "id": int(m.group(1)),
            "title": h3_a.get_text(strip=True),
            "url": url,
            "published_at": date_span.get_text(strip=True) if date_span else "",
        })
    return articles


def fetch_new_articles(state_path: str) -> list[dict]:
    """
    Fetch the headlines page and return articles newer than the last run
    (oldest first), then update the state file with the new high-water mark.

    On the very first run (no state file yet), this establishes a baseline
    and returns an empty list rather than flooding the first notification
    with the site's entire current headline backlog.
    """
    is_first_run = not os.path.exists(state_path)
    state = _load_state(state_path)
    last_seen_id = state.get("last_seen_id", 0)

    resp = requests.get(
        HEADLINES_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; EyewearNewsBot/1.0)"},
        timeout=20,
    )
    resp.raise_for_status()

    articles = _parse_articles(resp.text)
    if not articles:
        logger.warning("No articles parsed from %s — page structure may have changed.", HEADLINES_URL)
        return []

    max_id = max(a["id"] for a in articles)

    if is_first_run:
        logger.info("First run — recording baseline id %d, no notification sent.", max_id)
        _save_state(state_path, {"last_seen_id": max_id})
        return []

    new_articles = sorted(
        (a for a in articles if a["id"] > last_seen_id),
        key=lambda a: a["id"],
    )

    if max_id > last_seen_id:
        _save_state(state_path, {"last_seen_id": max_id})

    return new_articles
