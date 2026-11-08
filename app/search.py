from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_POLISH_STROKED_L = str.maketrans({"ł": "l", "Ł": "L"})
_TOKEN_SEPARATORS = re.compile(r"[\s,]+")


def normalize(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").translate(_POLISH_STROKED_L))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def expand_slash_numbers(number: str | None) -> list[str]:
    if not number:
        return []
    base, *suffixes = number.split("/")
    return [base] + [base[: len(base) - len(s)] + s for s in suffixes if s]


@dataclass(frozen=True, slots=True)
class TrainQuery:
    tokens: tuple[str, ...]

    @classmethod
    def parse(cls, raw: str) -> "TrainQuery":
        return cls(tuple(t for t in _TOKEN_SEPARATORS.split(normalize(raw)) if t))

    def matches(self, name: str | None, number: str | None, category: str | None) -> bool:
        if not self.tokens:
            return False
        normalized_name = normalize(name)
        numbers = expand_slash_numbers(normalize(number))
        normalized_category = normalize(category)
        return all(
            token in normalized_name
            or token == normalized_category
            or any(n.startswith(token) for n in numbers)
            for token in self.tokens
        )
