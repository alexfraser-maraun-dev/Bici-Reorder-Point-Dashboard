"""Post-run Slack dispatch for price intelligence.

Called best-effort from scrape_runner._run's finally block. Two channels:
  - priority pings (MAP violations + undercuts): one red message each, to the
    alerts webhook (falls back to the main webhook);
  - a single digest message (LLM market summary + notable moves + stock changes)
    to the main webhook, plus a health alert when a run fails or is partial.

Everything here swallows its own errors — the caller also wraps the whole call —
so notification problems never affect scrape data.
"""
from datetime import datetime

from app.services.notifications import slack
from . import repository, settings

# Individual priority pings are capped so an initial competitor-onboarding night
# (many first-sighting undercuts) can't flood the channel; the rest roll up into
# one summary line. Caps, webhooks, and per-message toggles are all runtime
# settings (admin console), defaulting to the PI_SLACK_* env vars.

# Event types that fire their own priority ping. Undercut events are still
# generated (they show in the change feed and give the digest context), but they
# no longer ping — only MAP violations do.
_ALERT_META = {
    "map_violation": ("⚠️ MAP violation", "below our MAP", "#d21f3c"),
}


def _fmt(v) -> str:
    return "—" if v is None else f"${float(v):,.2f}"


def _pct(v) -> str:
    if v is None:
        return ""
    sign = "+" if v > 0 else ""
    return f" ({sign}{v:.1f}%)"


def _title(item_title, item_brand) -> str:
    title = item_title or "Untitled item"
    return f"{title} — {item_brand}" if item_brand else title


def _hooks():
    """(main webhook, alerts webhook) from effective settings; alerts falls back
    to main, mirroring the original env-var behavior."""
    main = settings.get("slack_webhook_url")
    return main, (settings.get("slack_alerts_webhook_url") or main)


def dispatch_run(run_id: str, status: str, counters: dict, errors: list):
    main_hook, alerts_hook = _hooks()
    if not (settings.get("slack_enabled") and main_hook):
        return

    # Health alert first — it must fire even if the run failed before events landed.
    if status in ("failed", "partial") and settings.get("slack_health_alerts"):
        _post_health(status, counters, errors, main_hook)

    events = []
    try:
        events = repository.get_change_events(days=2, run_id=run_id, limit=1000)
    except Exception as e:
        print(f"pi: notify could not load events for {run_id}: {e}")

    notified_ids = []
    if settings.get("slack_map_pings"):
        notified_ids += _post_priority_pings(events, alerts_hook)
    if settings.get("slack_send_digest"):
        notified_ids += _post_digest(run_id, events, main_hook)

    if notified_ids:
        try:
            repository.mark_events_notified(notified_ids)
        except Exception as e:
            print(f"pi: notify could not mark events notified: {e}")


def _post_priority_pings(events, webhook) -> list:
    max_pings = settings.get("slack_max_priority_pings")
    priority = [e for e in events if e.get("event_type") in _ALERT_META]
    if not priority:
        return []
    # Sharpest first: biggest crossing (most negative pct) at the top.
    priority.sort(key=lambda e: (e.get("pct_change") if e.get("pct_change") is not None else 0))

    our_prices = _our_price_lookup([e.get("item_id") for e in priority])
    sent = []
    for e in priority[:max_pings]:
        label, relation, color = _ALERT_META[e["event_type"]]
        title = f"{label} — {e.get('competitor_name') or 'Competitor'}"
        our = our_prices.get(str(e.get("item_id")))
        lines = [
            f"*{_title(e.get('item_title'), e.get('item_brand'))}*",
            f"Their price: *{_fmt(e.get('new_price'))}*"
            + (f"  (was {_fmt(e.get('old_price'))}{_pct(e.get('pct_change'))})"
               if e.get("old_price") is not None else "")
            + f" — {relation}",
        ]
        if our is not None:
            lines.append(f"Our price: {_fmt(our)}")
        if e.get("url"):
            lines.append(f"<{e['url']}|View listing>")
        if slack.post_alert(webhook, fallback=title, title=title,
                            body="\n".join(lines), color=color):
            sent.append(e["event_id"])

    overflow = len(priority) - max_pings
    if overflow > 0:
        slack.post(webhook, text=f"…and *{overflow}* more MAP alerts this run.")
        sent += [e["event_id"] for e in priority[max_pings:]]
    return sent


def _post_digest(run_id, events, webhook) -> list:
    max_moves = settings.get("slack_max_digest_moves")
    digest_md = ""
    try:
        row = repository.get_digest_for_run(run_id)
        digest_md = (row or {}).get("digest_md") or ""
    except Exception as e:
        print(f"pi: notify could not load digest for {run_id}: {e}")

    moves = [e for e in events if e.get("event_type") in ("price_drop", "price_increase")]
    moves.sort(key=lambda e: abs(e.get("pct_change") or 0), reverse=True)
    # Competitor stock changes (out_of_stock/back_in_stock) are intentionally kept
    # out of Slack; they still land in the change feed.

    if not digest_md and not moves:
        return []

    blocks = [slack.header(f"🗞️ Price intel — {datetime.now().strftime('%b %-d')}")]
    if digest_md:
        blocks.append(slack.section(slack.to_mrkdwn(digest_md)))

    move_ids = []
    if moves:
        blocks.append(slack.divider())
        lines = ["*Notable moves* (matched items)"]
        for e in moves[:max_moves]:
            arrow = "🔻" if e["event_type"] == "price_drop" else "🔺"
            lines.append(
                f"{arrow} *{_title(e.get('item_title'), e.get('item_brand'))}* — "
                f"{e.get('competitor_name') or 'Competitor'}: "
                f"{_fmt(e.get('old_price'))} → {_fmt(e.get('new_price'))}{_pct(e.get('pct_change'))}"
            )
        if len(moves) > max_moves:
            lines.append(f"_…and {len(moves) - max_moves} more._")
        blocks.append(slack.section("\n".join(lines)))
        move_ids = [e["event_id"] for e in moves]

    fallback = "Price intel digest"
    if slack.post(webhook, text=fallback, blocks=blocks):
        return move_ids
    return []


def _post_health(status, counters, errors, webhook):
    label = "❌ Scrape failed" if status == "failed" else "⚠️ Scrape partial"
    counters = counters or {}
    lines = [
        f"*{label}*",
        f"Competitors: {counters.get('competitors_done', 0)} · "
        f"URLs: {counters.get('urls_done', 0)} · "
        f"Observations: {counters.get('observations', 0)} · "
        f"Changes: {counters.get('changes', 0)}",
    ]
    if errors:
        shown = "; ".join(str(x) for x in errors[:5])
        lines.append(f"Errors: {shown[:800]}")
    slack.post(webhook, text=label,
               attachments=[{"color": "#d21f3c", "fallback": label,
                             "blocks": [slack.section("\n".join(lines))]}])


def send_test() -> dict:
    """Fire a representative digest + one MAP ping + one undercut ping through the
    real Slack client so a user can confirm their webhooks/env before a scrape.
    Self-contained (no BigQuery). Returns a per-message success report."""
    main_hook, alerts_hook = _hooks()
    if not (settings.get("slack_enabled") and main_hook):
        return {
            "sent": False,
            "reason": "Slack disabled or no webhook configured — enable Slack and "
                      "set a webhook URL in the Admin tab (or via PI_SLACK_* env vars).",
        }

    map_ok = slack.post_alert(
        alerts_hook, fallback="MAP violation test",
        title="⚠️ MAP violation — Competitor Bikes Inc",
        body="\n".join([
            "*Sample Helmet M — Acme*",
            "Their price: *$89.00*  (was $109.00 (-18.3%)) — below our MAP",
            "Our price: $109.00",
        ]),
        color="#d21f3c",
    )

    digest_md = (
        "## Market position\n"
        "We're **cheapest** on 3 of 5 sampled items; pricier on 1.\n\n"
        "## Notable competitor moves\n"
        "- Competitor Bikes Inc dropped the Sample Road Bike to $1,799.\n\n"
        "## Suggested actions\n"
        "- Review the Sample Road Bike (we're $100 above market).\n\n"
        "_This is a test message from the price-intel notification flow._"
    )
    blocks = [
        slack.header(f"🗞️ Price intel — {datetime.now().strftime('%b %-d')} (test)"),
        slack.section(slack.to_mrkdwn(digest_md)),
        slack.divider(),
        slack.section("\n".join([
            "*Notable moves* (matched items)",
            "🔻 *Sample Road Bike 54cm — Acme* — Competitor Bikes Inc: "
            "$1,999.00 → $1,799.00 (-10.0%)",
            "🔺 *Sample Tire — Acme* — Other Store: $59.00 → $64.00 (+8.5%)",
        ])),
    ]
    digest_ok = slack.post(main_hook, text="Price intel digest (test)",
                           blocks=blocks)

    return {
        "sent": True,
        "alerts_webhook": "alerts" if settings.get("slack_alerts_webhook_url") else "main (fallback)",
        "results": {
            "map_ping": map_ok,
            "digest": digest_ok,
        },
    }


def _our_price_lookup(item_ids) -> dict:
    ids = {str(i) for i in item_ids if i}
    if not ids:
        return {}
    try:
        products = repository.get_tracked_products(include_archived=True)
    except Exception as e:
        print(f"pi: notify could not load tracked products: {e}")
        return {}
    out = {}
    for p in products:
        pid = str(p.get("item_id"))
        if pid in ids:
            out[pid] = p.get("map_price") or p.get("current_retail")
    return out
