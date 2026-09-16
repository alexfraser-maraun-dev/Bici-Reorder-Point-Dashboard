"""Post-run Slack dispatch for price intelligence.

Called best-effort from scrape_runner._run's finally block. Each message sits
behind its own runtime setting (admin console, defaulting to the PI_SLACK_*
env vars):
  - health alert when a run fails or is partial (main webhook);
  - priority pings, one red message each, to the alerts webhook (falls back
    to main): MAP violations, plus undercuts when opted in;
  - the LLM digest narrative (main webhook);
  - "Competitor price changes": the run's price moves rolled up to one line
    per product (matrix or item) × store (main webhook).

A store marked mute_slack in its settings is dropped from all of the above
without leaving the change feed. Everything here swallows its own errors — the
caller also wraps the whole call — so notification problems never affect
scrape data.
"""
from datetime import datetime

from app.services.notifications import slack
from . import change_groups, repository, settings
from .change_groups import fmt_pct, fmt_price

# Event types that fire their own priority ping: (title, relation, colour,
# setting that enables it). Undercut is off by default — it's recorded like any
# event (change feed, digest context) but only pings when opted in.
_ALERT_META = {
    "map_violation": ("⚠️ MAP violation", "below our MAP", "#d21f3c", "slack_map_pings"),
    "undercut": ("📉 Undercut", "below our price", "#e8710a", "slack_undercut_pings"),
}
_ALERT_ORDER = list(_ALERT_META)  # MAP first when both compete for the cap

# Only these families reach Slack. first_observation/new_match are pre-acked
# onboarding noise that would otherwise crowd the load limit on a new store's
# first night; stock changes stay out of Slack by design (2026-07-09).
_SLACK_EVENT_TYPES = ("price_drop", "price_increase", "map_violation", "undercut")
_EVENT_LOAD_LIMIT = 3000


def _pct_paren(v) -> str:
    return f" ({fmt_pct(v)})" if v is not None else ""


def _title(item_title, item_brand) -> str:
    title = item_title or "Untitled item"
    return f"{title} — {item_brand}" if item_brand else title


def _date_label() -> str:
    return datetime.now().strftime("%b %-d")


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

    events = _load_run_events(run_id)
    tracked_by_id = _tracked_by_id()

    notified_ids = []
    notified_ids += _post_priority_pings(events, alerts_hook, tracked_by_id)
    # Narrative before detail: the digest is the banner, the price changes the
    # line items under it.
    if settings.get("slack_send_digest") and settings.get("digest_enabled"):
        _post_digest(run_id, main_hook)
    if settings.get("slack_price_changes"):
        notified_ids += _post_price_changes(events, main_hook, tracked_by_id)

    if notified_ids:
        try:
            repository.mark_events_notified(notified_ids)
        except Exception as e:
            print(f"pi: notify could not mark events notified: {e}")


def _load_run_events(run_id: str) -> list:
    """The run's Slack-relevant events, minus Slack-muted stores. Feed mutes are
    already applied inside get_change_events; this one is additive and lives
    here because it only governs Slack."""
    try:
        events = repository.get_change_events(
            days=2, run_id=run_id, event_types=list(_SLACK_EVENT_TYPES),
            limit=_EVENT_LOAD_LIMIT)
    except Exception as e:
        print(f"pi: notify could not load events for {run_id}: {e}")
        return []
    if len(events) >= _EVENT_LOAD_LIMIT:
        print(f"pi: notify hit the {_EVENT_LOAD_LIMIT}-event load limit for "
              f"{run_id}; some changes won't reach Slack")
    try:
        muted = repository.slack_muted_competitor_ids()
    except Exception as e:
        print(f"pi: notify could not load Slack mutes: {e}")
        muted = set()
    if muted:
        events = [e for e in events if str(e.get("competitor_id")) not in muted]
    return events


def _tracked_by_id() -> dict:
    """Tracked rows keyed by item_id, archived included — an event can outlive
    its item's active status. Loaded once per dispatch and shared by the pings
    ("our price") and the rollup (matrix identity, variant counts)."""
    try:
        return {str(p.get("item_id")): p
                for p in repository.get_tracked_products(include_archived=True)}
    except Exception as e:
        print(f"pi: notify could not load tracked products: {e}")
        return {}


def _post_priority_pings(events, webhook, tracked_by_id) -> list:
    max_pings = settings.get("slack_max_priority_pings")
    enabled = {t for t, meta in _ALERT_META.items() if settings.get(meta[3])}
    priority = [e for e in events if e.get("event_type") in enabled]
    if not priority:
        return []
    # MAP before undercut, then sharpest first: biggest crossing (most negative
    # pct) at the top of each family.
    priority.sort(key=lambda e: (
        _ALERT_ORDER.index(e["event_type"]),
        e.get("pct_change") if e.get("pct_change") is not None else 0))

    sent = []
    for e in priority[:max_pings]:
        label, relation, color, _ = _ALERT_META[e["event_type"]]
        title = f"{label} — {e.get('competitor_name') or 'Competitor'}"
        item = tracked_by_id.get(str(e.get("item_id"))) or {}
        lines = [
            f"*{_title(e.get('item_title'), e.get('item_brand'))}*",
            f"Their price: *{fmt_price(e.get('new_price'))}*"
            + (f"  (was {fmt_price(e.get('old_price'))}{_pct_paren(e.get('pct_change'))})"
               if e.get("old_price") is not None else "")
            + f" — {relation}",
        ]
        # MAP compares against the MAP floor (map_price, else retail for tagged
        # items); undercut compares against our retail — show the number the
        # event was actually judged against.
        if e["event_type"] == "map_violation":
            our, our_label = item.get("map_price") or item.get("current_retail"), "Our MAP floor"
        else:
            our, our_label = item.get("current_retail"), "Our price"
        if our is not None:
            lines.append(f"{our_label}: {fmt_price(our)}")
        if e.get("url"):
            lines.append(f"<{e['url']}|View listing>")
        if slack.post_alert(webhook, fallback=title, title=title,
                            body="\n".join(lines), color=color):
            sent.append(e["event_id"])

    overflow = len(priority) - max_pings
    if overflow > 0:
        slack.post(webhook, text=f"…and *{overflow}* more alerts this run.")
        sent += [e["event_id"] for e in priority[max_pings:]]
    return sent


def _post_digest(run_id, webhook) -> bool:
    """The LLM narrative only. Price moves have their own message now."""
    try:
        row = repository.get_digest_for_run(run_id)
    except Exception as e:
        print(f"pi: notify could not load digest for {run_id}: {e}")
        return False
    digest_md = (row or {}).get("digest_md") or ""
    if not digest_md:
        return False
    blocks = [slack.header(f"🗞️ Price intel digest — {_date_label()}"),
              slack.section(slack.to_mrkdwn(digest_md))]
    return slack.post(webhook, text="Price intel digest", blocks=blocks)


def _post_price_changes(events, webhook, tracked_by_id) -> list:
    """One message: the run's price drops/increases rolled up per product ×
    store (change_groups). Returns the event ids the message covered."""
    groups = change_groups.group_price_changes(
        events, tracked_by_id,
        min_abs_pct=settings.get("slack_price_change_min_pct"),
        directions=settings.get("slack_price_change_directions"))
    if not groups:
        return []
    text, blocks, covered = change_groups.build_price_change_message(
        groups, settings.get("slack_max_price_changes"), _date_label())
    if slack.post(webhook, text=text, blocks=blocks):
        return covered
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


def _sample_price_changes():
    """Synthetic events + tracked rows through the real rollup: a partially
    changed matrix with ranges, and a standalone item."""
    now = datetime.now().isoformat()
    tracked = {
        "1": {"item_id": "1", "item_matrix_id": "m1", "matrix_description": "SuperSix EVO 5",
              "brand": "Cannondale", "title": "SuperSix EVO 5", "attribute_1": "54cm",
              "current_retail": 2999.0},
        "2": {"item_id": "2", "item_matrix_id": "m1", "matrix_description": "SuperSix EVO 5",
              "brand": "Cannondale", "title": "SuperSix EVO 5", "attribute_1": "56cm",
              "current_retail": 2999.0},
        "3": {"item_id": "3", "item_matrix_id": "m1", "matrix_description": "SuperSix EVO 5",
              "brand": "Cannondale", "title": "SuperSix EVO 5", "attribute_1": "58cm",
              "current_retail": 2999.0},
        "9": {"item_id": "9", "item_matrix_id": None, "title": "Grand Prix 5000 700x28",
              "brand": "Continental", "current_retail": 64.99},
    }
    events = [
        {"event_id": "test-1", "event_type": "price_increase", "item_id": "1",
         "competitor_id": "c1", "competitor_name": "Competitor Bikes Inc",
         "old_price": 2666.61, "new_price": 3199.93, "pct_change": 20.0,
         "url": "https://example.com/supersix-evo-5", "occurred_at": now},
        {"event_id": "test-2", "event_type": "price_increase", "item_id": "2",
         "competitor_id": "c1", "competitor_name": "Competitor Bikes Inc",
         "old_price": 2699.0, "new_price": 3249.0, "pct_change": 20.4,
         "url": "https://example.com/supersix-evo-5", "occurred_at": now},
        {"event_id": "test-3", "event_type": "price_drop", "item_id": "9",
         "competitor_id": "c2", "competitor_name": "Other Store",
         "old_price": 69.99, "new_price": 63.99, "pct_change": -8.57,
         "url": "https://example.com/gp5000", "occurred_at": now},
    ]
    return change_groups.group_price_changes(events, tracked)


def send_test() -> dict:
    """Fire a representative message of each kind through the real Slack
    client so a user can confirm their webhooks before a scrape. Self-contained
    (no BigQuery). Returns a per-message success report."""
    main_hook, alerts_hook = _hooks()
    if not (settings.get("slack_enabled") and main_hook):
        return {
            "sent": False,
            "reason": "Slack disabled or no webhook configured — enable Slack and "
                      "set a webhook URL in the Admin tab (or via PI_SLACK_* env vars).",
        }

    results = {}
    results["map_ping"] = slack.post_alert(
        alerts_hook, fallback="MAP violation test",
        title="⚠️ MAP violation — Competitor Bikes Inc",
        body="\n".join([
            "*Sample Helmet M — Acme*",
            "Their price: *$89.00*  (was $109.00 (-18.3%)) — below our MAP",
            "Our MAP floor: $109.00",
        ]),
        color="#d21f3c",
    )
    if settings.get("slack_undercut_pings"):
        results["undercut_ping"] = slack.post_alert(
            alerts_hook, fallback="Undercut test",
            title="📉 Undercut — Other Store",
            body="\n".join([
                "*Sample Tire — Acme*",
                "Their price: *$59.00*  (was $66.00 (-10.6%)) — below our price",
                "Our price: $64.99",
            ]),
            color="#e8710a",
        )

    text, blocks, _ = change_groups.build_price_change_message(
        _sample_price_changes(), 15, f"{_date_label()} (test)")
    results["price_changes"] = slack.post(main_hook, text=f"{text} (test)", blocks=blocks)

    digest_md = (
        "## Market position\n"
        "We're **cheapest** on 3 of 5 sampled items; pricier on 1.\n\n"
        "## Notable competitor moves\n"
        "- Competitor Bikes Inc raised the SuperSix EVO 5 to $3,199.\n\n"
        "## Suggested actions\n"
        "- Review the Sample Road Bike (we're $100 above market).\n\n"
        "_This is a test message from the price-intel notification flow._"
    )
    results["digest"] = slack.post(
        main_hook, text="Price intel digest (test)",
        blocks=[slack.header(f"🗞️ Price intel digest — {_date_label()} (test)"),
                slack.section(slack.to_mrkdwn(digest_md))])

    return {
        "sent": True,
        "alerts_webhook": "alerts" if settings.get("slack_alerts_webhook_url") else "main (fallback)",
        "results": results,
    }
