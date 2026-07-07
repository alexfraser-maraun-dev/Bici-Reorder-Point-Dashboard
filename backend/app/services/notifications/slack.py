"""Thin Slack Incoming-Webhook poster.

Best-effort by design: a Slack outage or a bad webhook must never fail the
caller (the price-intel scrape swallows notification errors the same way it
swallows digest errors). Provides small Block Kit helpers plus a markdown ->
Slack-mrkdwn normalizer so the LLM digest renders cleanly.
"""
import re

import requests

_TIMEOUT_SECONDS = 10


def header(text: str) -> dict:
    # Header blocks are plain_text only and cap at 150 chars.
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def section(mrkdwn: str) -> dict:
    # Section text tops out at 3000 chars; truncate defensively.
    return {"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn[:3000]}}


def divider() -> dict:
    return {"type": "divider"}


def to_mrkdwn(md: str) -> str:
    """Convert the small subset of Markdown the digest uses into Slack mrkdwn.

    Slack uses *bold* (single asterisk) and has no heading syntax, so **bold**
    collapses to *bold* and `#`/`##` headers become a bold line.
    """
    if not md:
        return ""
    out_lines = []
    for line in md.splitlines():
        stripped = line.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            out_lines.append(f"*{m.group(2).strip()}*")
            continue
        out_lines.append(line)
    text = "\n".join(out_lines)
    # **bold** / __bold__ -> *bold*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)
    # Markdown links [label](url) -> Slack <url|label>
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    return text


def post(webhook_url: str, *, text: str = None, blocks: list = None,
         attachments: list = None) -> bool:
    """Post a message to a Slack Incoming Webhook. Returns True on 2xx, False on
    any error — never raises."""
    if not webhook_url:
        return False
    payload = {}
    if text is not None:
        payload["text"] = text
    if blocks is not None:
        payload["blocks"] = blocks
    if attachments is not None:
        payload["attachments"] = attachments
    if not payload:
        return False
    try:
        resp = requests.post(webhook_url, json=payload, timeout=_TIMEOUT_SECONDS)
        if resp.status_code >= 300:
            print(f"slack: webhook returned {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"slack: post failed: {e}")
        return False


def post_alert(webhook_url: str, *, fallback: str, title: str, body: str,
               color: str = "#d21f3c") -> bool:
    """Post a single colored priority ping (uses a legacy attachment for the
    color bar, with a Block Kit body inside)."""
    return post(webhook_url, attachments=[{
        "color": color,
        "fallback": fallback,
        "blocks": [header(title), section(body)],
    }])
