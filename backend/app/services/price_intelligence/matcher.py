"""Matches scraped competitor products to our tracked products.

Cascade, highest confidence first:
  confirmed link (pi_product_links) -> GTIN/UPC exact (1.0) -> brand+SKU (0.9)
  -> fuzzy title >= 90 (0.75). Misses within a tracked brand additionally yield
  a *candidate* (best fuzzy hit in the 60-90 band, variant- or model-level) that
  the scrape runner records for LLM/human verification — confirmed candidates
  become links and match instantly on later runs.

UPC normalization strips non-digits and leading zeros on both sides (matches the
Merchant-API convention, so a 12-digit UPC equals its 13-digit zero-padded EAN).
Brand/title normalization folds accents ("Cervélo" == "Cervelo") and maps common
storefront aliases/suffixes ("Trek Bikes" -> "trek").
"""
import re
import unicodedata
from typing import Optional, Tuple

from rapidfuzz import fuzz, process

from . import config, repository, settings

FUZZY_THRESHOLD = 90
CANDIDATE_THRESHOLD = 60

# Apparel letter sizes normalized to a canonical token so "Large" == "L".
_LETTER_SIZES = {
    "xxs": "xxs", "xs": "xs", "s": "s", "sm": "s", "small": "s",
    "m": "m", "md": "m", "medium": "m", "l": "l", "lg": "l", "large": "l",
    "xl": "xl", "xlarge": "xl", "xxl": "xxl", "2xl": "xxl", "xxxl": "xxxl",
    "3xl": "xxxl", "os": "os", "onesize": "os",
}

# Storefront brand spellings that don't reduce to ours via folding/suffix-strip.
BRAND_ALIASES = {
    "specialized bicycle components": "specialized",
    "cervelo cycles": "cervelo",
    "shimano inc": "shimano",
    "sram corporation": "sram",
}

_BRAND_SUFFIX_RE = re.compile(r"\s+(bikes?|bicycles?|cycles?|cycling|sports?|usa|canada)$")

# Trailing variant descriptors Shopify appends to variant titles ("... - 56cm /
# Black") and bare size tokens, stripped when deriving a model-level title.
_VARIANT_TAIL_RE = re.compile(r"\s+-\s+[^-]{1,40}$")
_SIZE_TOKEN_RE = re.compile(
    r"\b(\d{2,3}\s?cm|xxs|xs|sm?|md?|lg?|xl|xxl|2xl|3xl|one size|os)\b", re.IGNORECASE
)


def _fold(value) -> str:
    """Lowercase + strip accents/diacritics."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip().lower()


def normalize_upc(value) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    digits = digits.lstrip("0")
    return digits or None


def _normalize_sku(value) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"[\s\-_.]", "", str(value)).lower()
    return cleaned or None


# Shopify puts a variant's *title* in the SKU slot when the merchant leaves the
# SKU blank — "Default Title" for single-variant products, or the bare option
# value ("Standard", "One Size"). These are not real SKUs: trusting one as a
# listing's identity collapses a store's entire SKU-less catalog onto a single
# match_key, so every product there then matches whatever that key was linked to
# (the Enroute "one item -> the whole catalog" fan-out). Normalized forms, to
# compare against _normalize_sku output. Extend as new sentinels surface.
_PLACEHOLDER_SKUS = {"defaulttitle", "standard", "onesize", "singleset"}


def _identifying_sku(value) -> Optional[str]:
    """The SKU usable as a listing's identity, or None for Shopify's blank-SKU
    sentinels (variant titles, not SKUs — see _PLACEHOLDER_SKUS)."""
    sku = _normalize_sku(value)
    if sku is None or sku in _PLACEHOLDER_SKUS:
        return None
    return sku


def _normalize_brand(value) -> Optional[str]:
    folded = _fold(value)
    if not folded:
        return None
    folded = BRAND_ALIASES.get(folded, folded)
    stripped = _BRAND_SUFFIX_RE.sub("", folded).strip()
    if stripped:
        folded = BRAND_ALIASES.get(stripped, stripped)
    return folded or None


def strip_variant_tokens(title: str) -> str:
    """Reduces a variant title toward its model name (drops trailing '- <size/color>'
    descriptors and bare size tokens)."""
    text = _VARIANT_TAIL_RE.sub("", title or "")
    text = _SIZE_TOKEN_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _attr_tokens(value) -> set:
    """Accent-folded word/number tokens of an attribute or option value."""
    return set(re.findall(r"[a-z0-9]+", _fold(value)))


def _canon_size(value):
    """Classifies a variant option / attribute as a size. Returns ('num', [digits])
    for numeric sizes ('58', '56cm', '700x28c'), ('letter', canon) for apparel
    sizes ('Large' -> 'l'), or None when the value isn't a size (i.e. it's a color
    or other descriptor)."""
    folded = _fold(value)
    if not folded:
        return None
    digits = re.findall(r"\d+", folded)
    if digits:
        return ("num", digits)
    key = folded.replace("-", "").replace(" ", "")
    if key in _LETTER_SIZES:
        return ("letter", _LETTER_SIZES[key])
    return None


def _size_value_matches(a, b) -> bool:
    """Two size values match when their digit sequences ('58' == '58cm') or their
    canonical letter sizes ('Large' == 'L') agree."""
    ca, cb = _canon_size(a), _canon_size(b)
    return ca is not None and ca == cb


def _color_match(a, b) -> bool:
    """Whether two color values plausibly refer to the same color. Shares a word
    token, OR one alnum-only spelling contains the other so spacing/camelCase
    variants agree ('blackSeries' == 'Black Series', 'Green' ~ 'Steel Green').
    Deliberately lenient: a false 'match' just keeps a link for human review;
    a false 'mismatch' would wrongly tombstone a real match."""
    if _attr_tokens(a) & _attr_tokens(b):
        return True
    ca = re.sub(r"[^a-z0-9]", "", _fold(a))
    cb = re.sub(r"[^a-z0-9]", "", _fold(b))
    return bool(ca) and bool(cb) and (ca in cb or cb in ca)


def attribute_match_score(options, attrs) -> float:
    """How well a competitor variant's options agree with our item's attributes.
    +1 per matching dimension (size/color); an exact color set beats a partial
    overlap (so 'Black' outranks 'Black & Transparent' for our 'Black' item); a
    hard conflict is penalized. Used to pick the best of several 'same variant'
    candidates competing for one (item, competitor) slot."""
    opts = [o for o in (options or []) if o and str(o).strip()]
    attrs = [a for a in (attrs or []) if a and str(a).strip()]
    if not opts or not attrs:
        return 0.0
    o_sz = [o for o in opts if _canon_size(o)]
    o_col = [o for o in opts if not _canon_size(o)]
    a_sz = [a for a in attrs if _canon_size(a)]
    a_col = [a for a in attrs if not _canon_size(a)]
    score = 0.0
    if o_sz and a_sz:
        score += 1.0 if any(_size_value_matches(o, a) for o in o_sz for a in a_sz) else -2.0
    if o_col and a_col:
        ot = set().union(*[_attr_tokens(o) for o in o_col])
        at = set().union(*[_attr_tokens(a) for a in a_col])
        if ot == at:
            score += 1.0        # exact color
        elif ot & at:
            score += 0.5        # overlaps but carries extra/missing color words
        else:
            score -= 2.0        # different color
    return score


def parse_variant_options(competitor_title):
    """Best-effort recovery of a competitor variant's options from its title, e.g.
    'Factor Monza Force - Steel Green / 58' -> ['Steel Green', '58']. Returns []
    when the title has no ' - Option / Option' tail (nothing to compare)."""
    if not competitor_title or " - " not in competitor_title:
        return []
    tail = competitor_title.rsplit(" - ", 1)[1]
    return [p.strip() for p in tail.split("/") if p.strip()]


def attributes_conflict(options, attrs) -> bool:
    """True when a competitor variant's options clearly conflict (size or color)
    with our item's attributes — the same conservative rule the matcher uses to
    suppress. Only fires on a comparable dimension that matches nothing; missing
    or unparseable data is never a conflict."""
    opts = [o for o in (options or []) if o and str(o).strip()]
    attrs = [a for a in (attrs or []) if a and str(a).strip()]
    if not opts or not attrs:
        return False
    o_sz = [o for o in opts if _canon_size(o)]
    o_col = [o for o in opts if not _canon_size(o)]
    a_sz = [a for a in attrs if _canon_size(a)]
    a_col = [a for a in attrs if not _canon_size(a)]
    if o_sz and a_sz and not any(_size_value_matches(o, a) for o in o_sz for a in a_sz):
        return True
    if o_col and a_col and not any(_color_match(o, a) for o in o_col for a in a_col):
        return True
    return False


def _model_codes(title) -> set:
    """Alphanumeric model-code tokens (letter+digit mixes like 'dx1000', 'sl8',
    'gp5000') — the tokens fuzzy title-similarity can't tell apart ('dx1000' vs
    'd1000' score ~identical). Used to block a fuzzy auto-match when the codes
    conflict. Pure-digit tokens (sizes, years) are excluded on purpose."""
    return {
        t for t in re.findall(r"[a-z0-9]+", _fold(title))
        if re.search(r"[a-z]", t) and re.search(r"\d", t)
    }


def size_matches_item(competitor_title, item_attrs):
    """Whether a competitor listing's size matches the item's size: True/False, or
    None when either side has no parseable size (can't tell). Used to keep a matrix's
    market data size-accurate for products whose sizes are priced differently (tires)."""
    a_sz = [a for a in (item_attrs or []) if a and _canon_size(a)]
    if not a_sz:
        return None
    o_sz = [o for o in parse_variant_options(competitor_title) if _canon_size(o)]
    if not o_sz:
        return None
    return any(_size_value_matches(o, a) for o in o_sz for a in a_sz)


def build_match_key(competitor_id, scraped: dict) -> str:
    """Stable identity for one scraped competitor listing — the key pi_product_links
    dedupes and re-attaches on. Prefers a real SKU (survives URL/handle renames),
    but ignores Shopify's blank-SKU sentinels (_identifying_sku) so a whole
    catalog's SKU-less listings don't collapse onto one key and fan out."""
    ident = (
        _identifying_sku(scraped.get("sku"))
        or scraped.get("url")
        or _fold(scraped.get("title"))
    )
    return f"{competitor_id}:{ident}"


class MatchIndex:
    """In-memory index over pi_tracked_products + confirmed pi_product_links
    (a few hundred rows each)."""

    def __init__(self, tracked_rows: list, links: list = None,
                 rejected_keys: set = None):
        self.by_upc = {}
        self.by_brand_sku = {}
        self.by_link = {}
        # Match keys a human rejected — never auto-match or re-propose them,
        # so a wrong fuzzy/UPC match stays gone instead of returning each scrape.
        self.rejected_keys = rejected_keys or set()
        self.titles = []       # parallel lists for rapidfuzz (variant grain)
        self.title_items = []
        self.model_titles = []  # model/matrix grain (one entry per matrix)
        self.model_items = []
        self.items = {}
        self.variants_by_matrix = {}  # matrix_id -> tracked sibling rows
        self.brands = set()
        seen_matrices = set()
        for row in tracked_rows:
            if row.get("excluded") or row.get("archived"):
                continue
            item_id = str(row["item_id"])
            self.items[item_id] = row
            mid = row.get("item_matrix_id")
            if mid:
                self.variants_by_matrix.setdefault(str(mid), []).append(row)
            upc = normalize_upc(row.get("upc_normalized"))
            if upc:
                self.by_upc[upc] = item_id
            brand = _normalize_brand(row.get("brand"))
            sku = _normalize_sku(row.get("sku"))
            if brand:
                self.brands.add(brand)
            if brand and sku:
                self.by_brand_sku[(brand, sku)] = item_id
            title = _fold(row.get("title"))
            if title:
                self.titles.append(title)
                self.title_items.append(item_id)
            # Model-level index: matrix description when the item belongs to a
            # matrix (first variant seen — rows arrive revenue-ranked — anchors
            # it), else the variant-token-stripped title when that differs.
            matrix_id = row.get("item_matrix_id")
            matrix_desc = _fold(row.get("matrix_description"))
            if matrix_id and matrix_desc:
                if matrix_id not in seen_matrices:
                    seen_matrices.add(matrix_id)
                    self.model_titles.append(matrix_desc)
                    self.model_items.append(item_id)
            elif title:
                stripped = _fold(strip_variant_tokens(row.get("title") or ""))
                if stripped and stripped != title:
                    self.model_titles.append(stripped)
                    self.model_items.append(item_id)
        for link in links or []:
            if (
                link.get("status") == "confirmed"
                and link.get("match_key")
                and link.get("item_id")
                # Links follow their item's lifecycle: an archived item's links
                # are frozen (no matching, no events) until it's re-tracked.
                and str(link["item_id"]) in self.items
            ):
                confidence = float(link.get("confidence") or 0.9)
                self.by_link[link["match_key"]] = (str(link["item_id"]), confidence)

    @classmethod
    def load(cls) -> "MatchIndex":
        return cls(
            repository.get_tracked_products(include_excluded=True),
            links=repository.get_product_links(status="confirmed", limit=5000),
            rejected_keys=repository.get_rejected_match_keys(),
        )

    def has_brand(self, brand) -> bool:
        normalized = _normalize_brand(brand)
        return bool(normalized) and normalized in self.brands

    def _resolve_by_attributes(self, options, anchor_id):
        """Given a competitor variant's structured options (e.g. ['Anodized Black',
        '58']) and the model anchor we'd otherwise propose, decide which tracked
        variant it really is by comparing color/size against our attribute_1/2/3.

        Returns one of:
          ("confirm", item_id)   - color AND size match exactly one tracked variant
          ("candidate", item_id) - one dimension matches, the other is unknown
          ("suppress", None)     - a dimension clearly conflicts (wrong color/size)
                                   with every comparable tracked variant
          (None, None)           - not enough structured signal; use fuzzy fallback
        """
        anchor = self.items.get(str(anchor_id))
        if not anchor:
            return (None, None)
        matrix_id = anchor.get("item_matrix_id")
        siblings = self.variants_by_matrix.get(str(matrix_id)) if matrix_id else None
        if not siblings:
            siblings = [anchor]

        opt_sizes = [o for o in options if _canon_size(o)]
        opt_colors = [o for o in options if o and not _canon_size(o)]
        if not opt_sizes and not opt_colors:
            return (None, None)

        both, partial = [], []
        size_comparable = size_hit = False
        color_comparable = color_hit = False
        for s in siblings:
            attrs = [a for a in (s.get("attribute_1"), s.get("attribute_2"),
                                 s.get("attribute_3")) if a and str(a).strip()]
            s_sizes = [a for a in attrs if _canon_size(a)]
            s_colors = [a for a in attrs if not _canon_size(a)]
            size_state = None
            if opt_sizes and s_sizes:
                size_comparable = True
                size_state = any(_size_value_matches(o, a)
                                 for o in opt_sizes for a in s_sizes)
                size_hit = size_hit or size_state
            color_state = None
            if opt_colors and s_colors:
                color_comparable = True
                color_state = any(_color_match(o, a)
                                  for o in opt_colors for a in s_colors)
                color_hit = color_hit or color_state
            if size_state and color_state:
                both.append(str(s["item_id"]))
            elif (size_state and color_state is None) or (color_state and size_state is None):
                partial.append(str(s["item_id"]))

        if len(both) == 1:
            return ("confirm", both[0])
        if both or partial:
            # Exact-but-ambiguous, or one dimension matched — let a human confirm.
            return ("candidate", (both or partial)[0])
        # No supported variant. Suppress only on a conflict we could actually see
        # (a dimension was comparable and matched nothing) — ambiguity falls back.
        if (size_comparable and not size_hit) or (color_comparable and not color_hit):
            return ("suppress", None)
        return (None, None)

    def match(self, scraped: dict, match_key: str = None):
        """Returns (item_id, method, confidence, candidate).

        Confirmed links always match. The other tiers (GTIN / brand+SKU /
        fuzzy>=90 / attr-exact) auto-match only when PI_AUTO_CONFIRM is on;
        otherwise every hit comes back as a *proposal* candidate
        {item_id, method, confidence, fuzzy_score, level} for the Matching
        queue — observations stay unmatched until a human confirms the link.
        """
        if match_key and match_key in self.rejected_keys:
            # A human rejected this listing — never re-match it (auto or candidate).
            return None, None, 0.0, None

        if match_key and match_key in self.by_link:
            item_id, confidence = self.by_link[match_key]
            return item_id, "link", confidence, None

        # Runtime-editable via the admin console; defaults to PI_AUTO_CONFIRM.
        auto_confirm = settings.get("auto_confirm")

        gtin = normalize_upc(scraped.get("gtin"))
        if gtin and gtin in self.by_upc:
            if auto_confirm:
                return self.by_upc[gtin], "gtin", 1.0, None
            return None, None, 0.0, {
                "item_id": self.by_upc[gtin], "method": "gtin",
                "confidence": 1.0, "fuzzy_score": None, "level": "variant",
            }

        brand = _normalize_brand(scraped.get("brand"))
        sku = _normalize_sku(scraped.get("sku"))
        if brand and sku and (brand, sku) in self.by_brand_sku:
            if auto_confirm:
                return self.by_brand_sku[(brand, sku)], "brand_sku", 0.9, None
            return None, None, 0.0, {
                "item_id": self.by_brand_sku[(brand, sku)], "method": "brand_sku",
                "confidence": 0.9, "fuzzy_score": None, "level": "variant",
            }

        title = _fold(scraped.get("title"))
        # Only fuzzy-match within a known brand: cross-brand title collisions
        # ("Floor Pump") are the main source of false positives.
        if not title or (brand and brand not in self.brands):
            return None, None, 0.0, None

        variant_best = None
        if self.titles:
            variant_best = process.extractOne(
                title, self.titles, scorer=fuzz.token_sort_ratio,
                score_cutoff=CANDIDATE_THRESHOLD,
            )
        if variant_best and variant_best[1] >= FUZZY_THRESHOLD:
            _, score, idx = variant_best
            # Model-code guard: don't silently auto-match when the titles carry
            # conflicting model codes (our "DX1000" vs their "D1000") — the tokens
            # fuzzy scoring can't distinguish. Demote to a review candidate instead.
            sc, tc = _model_codes(title), _model_codes(self.titles[idx])
            if not (sc and tc and sc.isdisjoint(tc)):
                if auto_confirm:
                    return self.title_items[idx], "fuzzy_title", round(score / 100 * 0.8, 3), None
                return None, None, 0.0, {
                    "item_id": self.title_items[idx], "method": "fuzzy_title",
                    "confidence": round(score / 100 * 0.8, 3),
                    "fuzzy_score": round(float(score), 1), "level": "variant",
                }

        # No auto-match: surface the best near-miss (variant or model grain) as
        # a verification candidate. Model hits are never auto-matched — sizes
        # and model years are exactly what fuzzy scores can't distinguish.
        candidate = None
        if brand:
            model_best = None
            if self.model_titles:
                model_best = process.extractOne(
                    _fold(strip_variant_tokens(scraped.get("title") or "")),
                    self.model_titles, scorer=fuzz.token_sort_ratio,
                    score_cutoff=CANDIDATE_THRESHOLD,
                )
            best = None
            if model_best and (not variant_best or model_best[1] >= variant_best[1]):
                best = ("model", self.model_items[model_best[2]], model_best[1])
            elif variant_best:
                best = ("variant", self.title_items[variant_best[2]], variant_best[1])
            if best:
                # Structured attribute pass (Shopify variants): route to the exact
                # tracked variant, or suppress a clear color/size mismatch, before
                # falling back to the fuzzy model/variant candidate.
                options = scraped.get("variant_options")
                if config.ATTR_MATCH_ENABLED and options:
                    verdict, target = self._resolve_by_attributes(options, best[1])
                    if verdict == "suppress":
                        return None, None, 0.0, None
                    # Colour/size agreement only proves "same variant" when the
                    # model anchor it was resolved against is itself credible.
                    # Against a weak anchor (a different model that merely shares
                    # a brand) the agreement is a coincidence, so the pair keeps
                    # its real fuzzy score and competes honestly for the review
                    # queue's per-item/per-run budget instead of topping the sort.
                    anchor_trusted = best[2] >= config.ATTR_ANCHOR_MIN_SCORE
                    if (verdict == "confirm" and anchor_trusted
                            and config.ATTR_AUTO_CONFIRM and auto_confirm):
                        return target, "attr_exact", 0.97, None
                    if verdict in ("confirm", "candidate"):
                        return None, None, 0.0, {
                            "item_id": target,
                            "method": "attr",
                            "confidence": (0.97 if verdict == "confirm" and anchor_trusted
                                           else None),
                            "fuzzy_score": round(float(best[2]), 1),
                            "level": "variant",
                        }
                candidate = {
                    "item_id": best[1],
                    "fuzzy_score": round(float(best[2]), 1),
                    "level": best[0],
                }
        return None, None, 0.0, candidate
