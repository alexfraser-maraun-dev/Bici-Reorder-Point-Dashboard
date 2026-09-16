"""Product × store rollup of price-change events.

Shared by the Slack "Competitor price changes" message and the change feed's
"Group by product" view, so the two agree by construction. Pure: no BigQuery
and no settings reads — callers pass the events and a tracked-product lookup
(`get_tracked_products(include_archived=True)` keyed by item_id), which is
what makes this unit-testable and cheap to call from the scrape thread.

Events carry no matrix columns (only a per-variant item_title), so the matrix
identity comes from the tracked row: a variant's item_matrix_id groups it with
its siblings, a standalone item groups on its own, and an unmatched tracked-URL
event (no item_id at all) groups on its URL.
"""
from collections import Counter, defaultdict

from app.services.notifications import slack

PRICE_EVENT_TYPES = ("price_drop", "price_increase")

_DIRECTION_TYPES = {
    "both": PRICE_EVENT_TYPES,
    "drops": ("price_drop",),
    "increases": ("price_increase",),
}

# A single line must never approach slack.section()'s 3000-char truncation —
# a cut inside <url|view> corrupts the rest of the section's mrkdwn.
_TITLE_MAX_CHARS = 80
_SECTION_CHAR_LIMIT = 2900
_MAX_SECTIONS = 40


def direction_event_types(choice) -> tuple:
    """Event types for a `slack_price_change_directions` choice. Unknown or
    stale values widen to both — a bad stored setting must not silence the
    message."""
    return _DIRECTION_TYPES.get(choice, PRICE_EVENT_TYPES)


# --- grouping ----------------------------------------------------------------

def _f(v):
    return None if v is None else float(v)


def _ts(value) -> str:
    """Sortable timestamp key: BigQuery rows carry datetimes, tests and JSON
    carry ISO strings; both sort chronologically as text."""
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _matrix_variant_totals(tracked: dict) -> dict:
    """Live (non-archived) variant count per matrix — the same rule as
    get_tracked_matrices_with_market's variants_total."""
    counts = Counter()
    for row in tracked.values():
        mid = row.get("item_matrix_id")
        if mid and not row.get("archived"):
            counts[str(mid)] += 1
    return counts


def _brand_prefix(title, brand) -> str:
    """"SuperSix EVO 5" → "Cannondale SuperSix EVO 5", without doubling a brand
    the title already carries."""
    title = (title or "").strip()
    brand = (brand or "").strip()
    if not title:
        return brand or "Untitled item"
    if brand and brand.lower() not in title.lower():
        return f"{brand} {title}"
    return title


def _variant_label(item, event):
    """"56cm / Black" from the tracked attributes, else the parenthesised
    suffix scrape_runner._item_display_title appends to event titles."""
    if item:
        attrs = [str(a).strip() for a in (item.get("attribute_1"), item.get("attribute_2"),
                                          item.get("attribute_3")) if a and str(a).strip()]
        if attrs:
            return " / ".join(attrs)
    title = (event.get("item_title") or "").strip()
    if title.endswith(")") and "(" in title:
        return title[title.rfind("(") + 1:-1].strip() or None
    return None


def group_price_changes(events, tracked_by_id, *, min_abs_pct=0, directions="both") -> list:
    """Roll price_drop/price_increase events up to one group per product
    (matrix / item / listing) × store.

    Filters (type, direction, min |%|) apply per event before grouping, so a
    "drops only" view of a matrix that moved both ways shows just its drops.
    The feed path already filters in SQL and passes the defaults; notify
    filters here because it holds the run's events in memory.

    Summary numbers come from the latest event per listing so a 100→90→95
    history inside the window reads as a net drop, not "mixed"; event_ids and
    events still carry every event (acknowledge + notified need them all).
    """
    allowed = set(direction_event_types(directions))
    threshold = float(min_abs_pct or 0)
    tracked = {str(k): v for k, v in (tracked_by_id or {}).items()}
    totals = _matrix_variant_totals(tracked)

    buckets = defaultdict(list)
    for e in events:
        if e.get("event_type") not in allowed:
            continue
        if abs(_f(e.get("pct_change")) or 0) < threshold:
            continue
        item_id = e.get("item_id")
        item = tracked.get(str(item_id)) if item_id else None
        if item and item.get("item_matrix_id"):
            product_key = f"matrix:{item['item_matrix_id']}"
        elif item_id:
            product_key = f"item:{item_id}"
        else:
            product_key = f"listing:{e.get('url') or e.get('event_id')}"
        competitor_key = str(e.get("competitor_id") or f"name:{e.get('competitor_name') or ''}")
        buckets[(product_key, competitor_key)].append(e)

    groups = [_build_group(pk, ck, evs, tracked, totals)
              for (pk, ck), evs in buckets.items()]
    groups.sort(key=lambda g: (-g["pct_abs_max"], -g["variants_changed"], g["title"]))
    return groups


def sort_by_recency(groups) -> list:
    """The feed's default order: latest change first (Slack keeps magnitude)."""
    return sorted(groups, key=lambda g: _ts(g["last_occurred_at"]), reverse=True)


def _build_group(product_key, competitor_key, evs, tracked, totals) -> dict:
    evs = sorted(evs, key=lambda e: _ts(e.get("occurred_at")), reverse=True)
    # Newest first, so the first event seen per listing is its latest.
    latest = {}
    for e in evs:
        latest.setdefault(str(e.get("item_id") or e.get("url") or e.get("event_id")), e)
    heads = list(latest.values())
    kind, _, product_id = product_key.partition(":")

    def item_of(e):
        return tracked.get(str(e.get("item_id"))) if e.get("item_id") else None

    sample = next((item_of(e) for e in heads if item_of(e)), None)
    first = heads[0]
    if kind == "matrix":
        title = sample.get("matrix_description") or sample.get("title") or first.get("item_title")
    elif sample:
        title = sample.get("title") or first.get("item_title")
    else:
        # Unknown to the tracked table (archived long ago, or an unmatched
        # tracked URL): keep the event's title verbatim — we can't tell whether
        # its "(attr / attr)" suffix is a matrix variant.
        title = first.get("item_title") or first.get("url") or "Unmatched listing"
    brand = (sample or {}).get("brand") or first.get("item_brand")

    types = {e.get("event_type") for e in heads}
    direction = "mixed" if len(types) > 1 else ("drop" if "price_drop" in types else "increase")

    pcts = [_f(e["pct_change"]) for e in heads if e.get("pct_change") is not None]
    news = [_f(e["new_price"]) for e in heads if e.get("new_price") is not None]
    olds = [_f(e["old_price"]) for e in heads if e.get("old_price") is not None]
    ours = [_f(item_of(e)["current_retail"]) for e in heads
            if item_of(e) and item_of(e).get("current_retail") is not None]
    item_ids = {str(e["item_id"]) for e in heads if e.get("item_id")}
    headline = max(heads, key=lambda e: abs(_f(e.get("pct_change")) or 0))
    unread = sum(1 for e in evs if not e.get("acknowledged"))

    return {
        "group_key": f"{product_key}|c:{competitor_key}",
        "product_kind": kind,
        "item_matrix_id": product_id if kind == "matrix" else None,
        "item_id": product_id if kind == "item" else None,
        "title": _brand_prefix(title, brand),
        "brand": brand,
        "competitor_id": first.get("competitor_id"),
        "competitor_name": first.get("competitor_name"),
        "direction": direction,
        "variants_changed": len(item_ids) or 1,
        "variants_total": totals.get(product_id) if kind == "matrix" else None,
        "pct_min": min(pcts) if pcts else None,
        "pct_max": max(pcts) if pcts else None,
        "pct_abs_max": max((abs(p) for p in pcts), default=0.0),
        "new_price_min": min(news) if news else None,
        "new_price_max": max(news) if news else None,
        "old_price_min": min(olds) if olds else None,
        "old_price_max": max(olds) if olds else None,
        "our_price_min": min(ours) if ours else None,
        "our_price_max": max(ours) if ours else None,
        "url": headline.get("url"),
        "first_occurred_at": evs[-1].get("occurred_at"),
        "last_occurred_at": evs[0].get("occurred_at"),
        "unread_count": unread,
        "acknowledged": unread == 0,
        "event_ids": [e["event_id"] for e in evs],
        "events": [{
            "event_id": e.get("event_id"),
            "item_id": e.get("item_id"),
            "variant": _variant_label(item_of(e), e),
            "event_type": e.get("event_type"),
            "old_price": _f(e.get("old_price")),
            "new_price": _f(e.get("new_price")),
            "pct_change": _f(e.get("pct_change")),
            "url": e.get("url"),
            "occurred_at": e.get("occurred_at"),
            "acknowledged": bool(e.get("acknowledged")),
        } for e in evs],
    }


# --- Slack formatting ----------------------------------------------------------

_MRKDWN_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def esc(text) -> str:
    """Slack mrkdwn escaping — `<` opens a link, so a title containing one
    would swallow the rest of the line."""
    return "".join(_MRKDWN_ESCAPES.get(ch, ch) for ch in str(text or ""))


def fmt_price(v) -> str:
    return "—" if v is None else f"${float(v):,.2f}"


def fmt_pct(v) -> str:
    """Signed, one decimal with a trailing .0 dropped: +20%, -8.5%."""
    if v is None:
        return ""
    r = round(float(v), 1)
    text = f"{r:.1f}".rstrip("0").rstrip(".")
    if r > 0:
        text = f"+{text}"
    return f"{text}%"


def _pct_range(lo, hi) -> str:
    if lo is None or hi is None:
        return fmt_pct(hi if lo is None else lo)
    if round(lo, 1) == round(hi, 1):
        return fmt_pct(hi)
    if (lo < 0) == (hi < 0):
        # Same direction: sign once, magnitudes ascending.
        a, b = sorted((abs(lo), abs(hi)))
        return f"{fmt_pct(-a if lo < 0 else a)[:-1]}–{fmt_pct(b).lstrip('+')}"
    return f"{fmt_pct(lo)} to {fmt_pct(hi)}"


def _price_range(lo, hi) -> str:
    if lo is None and hi is None:
        return ""
    if lo is None or hi is None or round(lo, 2) == round(hi, 2):
        return fmt_price(hi if lo is None else lo)
    return f"{fmt_price(lo)}–{fmt_price(hi)}"


def _truncate(text, limit) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def format_group_line(g: dict) -> str:
    """One mrkdwn line, e.g.
    🔺 *Cannondale SuperSix EVO 5* (6 variants) +20% → $3,199.93 (was $2,666.61)
    — Primeau Velo · ours $2,999.00 · <url|view>"""
    arrow = {"drop": "🔻", "increase": "🔺"}.get(g["direction"], "↕️")
    head = f"{arrow} *{esc(_truncate(g['title'], _TITLE_MAX_CHARS))}*"
    if g["product_kind"] == "matrix":
        n, total = g["variants_changed"], g["variants_total"]
        if total is None or n >= total:
            if n > 1 or (total or 0) > 1:
                head += f" ({n} variant{'s' if n != 1 else ''})"
        else:
            head += f" ({n} of {total} variants)"
    parts = [head]
    pct = _pct_range(g["pct_min"], g["pct_max"])
    if pct:
        parts.append(pct)
    new = _price_range(g["new_price_min"], g["new_price_max"])
    was = _price_range(g["old_price_min"], g["old_price_max"])
    parts.append(f"→ {new}" + (f" (was {was})" if was else ""))
    line = " ".join(parts) + f" — {esc(g.get('competitor_name') or 'Competitor')}"
    ours = _price_range(g["our_price_min"], g["our_price_max"])
    if ours:
        line += f" · ours {ours}"
    url = g.get("url")
    if url and not any(ch in url for ch in "<>|"):
        line += f" · <{url}|view>"
    return line


def _chunk_lines(lines, limit=_SECTION_CHAR_LIMIT) -> list:
    """Greedy line packing so every section stays under the limit with room
    to spare. A single over-long line (impossible after the title cap, but
    cheap to guard) is hard-cut."""
    chunks, current, size = [], [], 0
    for line in lines:
        if len(line) > limit:
            line = line[:limit - 1] + "…"
        extra = len(line) + (1 if current else 0)
        if current and size + extra > limit:
            chunks.append(current)
            current, size = [], 0
            extra = len(line)
        current.append(line)
        size += extra
    if current:
        chunks.append(current)
    return chunks


def build_price_change_message(groups, max_lines, date_label, *,
                               max_sections=_MAX_SECTIONS):
    """(fallback text, blocks, covered event ids) for the rollup message.

    `covered` spans every group — shown or folded into the "…and N more"
    line — matching how the priority pings mark their overflow: the user was
    told about them, just not line by line."""
    shown = groups[:max(int(max_lines or 0), 0)]
    chunks = _chunk_lines([format_group_line(g) for g in shown])[:max_sections]
    shown_count = sum(len(c) for c in chunks)
    overflow = len(groups) - shown_count

    events = [ev for g in groups for ev in g["events"]]
    drops = sum(1 for ev in events if ev["event_type"] == "price_drop")
    increases = sum(1 for ev in events if ev["event_type"] == "price_increase")
    stores = {g["competitor_id"] or g["competitor_name"] for g in groups}
    summary = (f"{len(events)} change{'s' if len(events) != 1 else ''} · "
               f"{len(groups)} product{'s' if len(groups) != 1 else ''} · "
               f"{len(stores)} store{'s' if len(stores) != 1 else ''} · "
               f"{drops}▼ {increases}▲")

    blocks = [slack.header(f"📊 Competitor price changes — {date_label}"),
              slack.context(summary)]
    blocks += [slack.section("\n".join(chunk)) for chunk in chunks]
    if overflow > 0:
        blocks.append(slack.context(f"_…and {overflow} more — see the change feed._"))
    text = f"Competitor price changes: {len(groups)} product{'s' if len(groups) != 1 else ''}"
    covered = [eid for g in groups for eid in g["event_ids"]]
    return text, blocks, covered
