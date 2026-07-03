"""Matches scraped competitor products to our tracked products.

Cascade, highest confidence first:
  GTIN/UPC exact (1.0) -> brand+SKU (0.9) -> fuzzy title >= 90 (0.75).
UPC normalization strips non-digits and leading zeros on both sides (matches the
Merchant-API convention, so a 12-digit UPC equals its 13-digit zero-padded EAN).
"""
import re
from typing import Optional, Tuple

from rapidfuzz import fuzz, process

from . import repository

FUZZY_THRESHOLD = 90


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
    if not value:
        return None
    return str(value).strip().lower() or None


class MatchIndex:
    """In-memory index over pi_tracked_products (a few hundred rows)."""

    def __init__(self, tracked_rows: list):
        self.by_upc = {}
        self.by_brand_sku = {}
        self.titles = []       # parallel lists for rapidfuzz
        self.title_items = []
        self.items = {}
        self.brands = set()
        for row in tracked_rows:
            if row.get("excluded"):
                continue
            item_id = str(row["item_id"])
            self.items[item_id] = row
            upc = normalize_upc(row.get("upc_normalized") or row.get("sku"))
            if upc:
                self.by_upc[upc] = item_id
            brand = _normalize_brand(row.get("brand"))
            sku = _normalize_sku(row.get("sku"))
            if brand:
                self.brands.add(brand)
            if brand and sku:
                self.by_brand_sku[(brand, sku)] = item_id
            title = (row.get("title") or "").strip()
            if title:
                self.titles.append(title.lower())
                self.title_items.append(item_id)

    @classmethod
    def load(cls) -> "MatchIndex":
        return cls(repository.get_tracked_products(include_excluded=True))

    def match(self, scraped: dict) -> Tuple[Optional[str], Optional[str], float]:
        """Returns (item_id, method, confidence) or (None, None, 0.0)."""
        gtin = normalize_upc(scraped.get("gtin"))
        if gtin and gtin in self.by_upc:
            return self.by_upc[gtin], "gtin", 1.0

        brand = _normalize_brand(scraped.get("brand"))
        sku = _normalize_sku(scraped.get("sku"))
        if brand and sku and (brand, sku) in self.by_brand_sku:
            return self.by_brand_sku[(brand, sku)], "brand_sku", 0.9

        title = (scraped.get("title") or "").strip().lower()
        if title and self.titles:
            # Only fuzzy-match within a known brand: cross-brand title collisions
            # ("Floor Pump") are the main source of false positives.
            if brand and brand not in self.brands:
                return None, None, 0.0
            best = process.extractOne(
                title, self.titles, scorer=fuzz.token_sort_ratio, score_cutoff=FUZZY_THRESHOLD
            )
            if best:
                _, score, idx = best
                return self.title_items[idx], "fuzzy_title", round(score / 100 * 0.8, 3)
        return None, None, 0.0
