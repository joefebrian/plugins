"""Shared types for brand/product mention extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProductMention:
    name: str
    mention_type: str
    category: str
    context: str
    confidence: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.mention_type,
            "category": self.category,
            "context": self.context,
            "confidence": self.confidence,
            "source": self.source,
        }


def dedupe_mentions(items: list[ProductMention]) -> list[ProductMention]:
    seen: set[str] = set()
    out: list[ProductMention] = []
    for item in sorted(items, key=lambda x: (-x.confidence, x.name)):
        key = item.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def merge_mention_lists(
    *groups: list[ProductMention],
) -> list[ProductMention]:
    """Merge multiple mention lists; boost confidence when same brand from multiple sources."""
    by_key: dict[str, ProductMention] = {}
    for group in groups:
        for item in group:
            key = item.name.lower()
            existing = by_key.get(key)
            if not existing:
                by_key[key] = item
                continue
            boosted = min(1.0, max(existing.confidence, item.confidence) + 0.12)
            sources = {existing.source, item.source}
            by_key[key] = ProductMention(
                name=existing.name if len(existing.name) >= len(item.name) else item.name,
                mention_type=existing.mention_type,
                category=existing.category or item.category,
                context=f"{existing.context} | {item.context}"[:240],
                confidence=boosted,
                source="+".join(sorted(sources)),
            )
    return dedupe_mentions(list(by_key.values()))