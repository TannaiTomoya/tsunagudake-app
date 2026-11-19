import hashlib
import json
import unicodedata
from pathlib import Path

from .types import Dictionary, NeedKind, Pattern, Translation


def load_dictionary() -> Dictionary:
    raw = json.loads(
        Path(__file__).with_name("dictionary_v1.json").read_text(encoding="utf-8")
    )
    return Dictionary(
        raw["version"],
        tuple(raw["taxwork"]),
        tuple(
            Pattern(NeedKind(item["kind"]), tuple(item["words"]), tuple(item["tasks"]))
            for item in raw["patterns"]
        ),
    )


def normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def _hits(text: str, words: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(word for word in words if (key := normalize(word)) and key in text)


def translate(text: str, dictionary: Dictionary) -> Translation:
    normalized = normalize(text)
    digest = hashlib.sha256((normalized + dictionary.version).encode()).hexdigest()
    blocked = _hits(normalized, dictionary.taxwork)
    if blocked:
        return Translation(digest, blocked=True, matched=False, blocked_words=blocked)
    matches = tuple(
        Pattern(pattern.kind, hits, pattern.tasks)
        for pattern in dictionary.patterns
        if (hits := _hits(normalized, pattern.words))
    )
    return Translation(digest, blocked=False, matched=bool(matches), patterns=matches)
