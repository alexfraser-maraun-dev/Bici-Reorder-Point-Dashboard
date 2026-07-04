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

from . import repository

FUZZY_THRESHOLD = 90
CANDIDATE_THRESHOLD = 60

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


def build_match_key(competitor_id, scraped: dict) -> str:
    """Stable identity for one scraped competitor listing — the key pi_product_links
    dedupes and re-attaches on. Prefers SKU (survives URL/handle renames)."""
    ident = (
        _normalize_sku(scraped.get("sku"))
        or scraped.get("url")
        or _fold(scraped.get("title"))
    )
    return f"{competitor_id}:{ident}"


class MatchIndex:
    """In-memory index over pi_tracked_products + confirmed pi_product_links
    (a few hundred rows each)."""

    def __init__(self, tracked_rows: list, links: list = None):
        self.by_upc = {}
        self.by_brand_sku = {}
        self.by_link = {}
        self.titles = []       # parallel lists for rapidfuzz (variant grain)
        self.title_items = []
        self.model_titles = []  # model/matrix grain (one entry per matrix)
        self.model_items = []
        self.items = {}
        self.brands = set()
        seen_matrices = set()
        for row in tracked_rows:
            if row.get("excluded"):
                continue
            item_id = str(row["item_id"])
            self.items[item_id] = row
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
            ):
                confidence = float(link.get("confidence") or 0.9)
                self.by_link[link["match_key"]] = (str(link["item_id"]), confidence)

    @classmethod
    def load(cls) -> "MatchIndex":
        return cls(
            repository.get_tracked_products(include_excluded=True),
            links=repository.get_product_links(status="confirmed", limit=5000),
        )

    def has_brand(self, brand) -> bool:
        normalized = _normalize_brand(brand)
        return bool(normalized) and normalized in self.brands

    def match(self, scraped: dict, match_key: str = None):
        """Returns (item_id, method, confidence, candidate).

        candidate is None on a match; on a miss within a tracked brand it is the
        best sub-threshold fuzzy hit: {item_id, fuzzy_score, level}.
        """
        if match_key and match_key in self.by_link:
            item_id, confidence = self.by_link[match_key]
            return item_id, "link", confidence, None

        gtin = normalize_upc(scraped.get("gtin"))
        if gtin and gtin in self.by_upc:
            return self.by_upc[gtin], "gtin", 1.0, None

        brand = _normalize_brand(scraped.get("brand"))
        sku = _normalize_sku(scraped.get("sku"))
        if brand and sku and (brand, sku) in self.by_brand_sku:
            return self.by_brand_sku[(brand, sku)], "brand_sku", 0.9, None

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
            return self.title_items[idx], "fuzzy_title", round(score / 100 * 0.8, 3), None

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
                candidate = {
                    "item_id": best[1],
                    "fuzzy_score": round(float(best[2]), 1),
                    "level": best[0],
                }
        return None, None, 0.0, candidate
