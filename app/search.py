from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_POLISH_STROKED_L = str.maketrans({"ł": "l", "Ł": "L"})
_TOKEN_SEPARATORS = re.compile(r"[\s,\-]+")


def normalize(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").translate(_POLISH_STROKED_L))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def tokenize(text: str | None) -> tuple[str, ...]:
    return tuple(t for t in _TOKEN_SEPARATORS.split(normalize(text)) if t)


def expand_slash_numbers(number: str | None) -> list[str]:
    if not number:
        return []
    base, *suffixes = number.split("/")
    return [base] + [base[: len(base) - len(s)] + s for s in suffixes if s]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    tokens: tuple[str, ...]

    @classmethod
    def parse(cls, raw: str) -> "SearchQuery":
        return cls(tokenize(raw))

    @property
    def phrase(self) -> str:
        return " ".join(self.tokens)

    def matches_train(self, name: str | None, number: str | None, category: str | None,
                      carrier_id: str | None = None) -> bool:
        if not self.tokens:
            return False
        normalized_name = normalize(name)
        numbers = expand_slash_numbers(normalize(number))
        labels = {normalize(category), normalize(carrier_id)} - {""}
        return all(
            token in normalized_name
            or token in labels
            or any(n.startswith(token) for n in numbers)
            for token in self.tokens
        )

    def matches_name(self, name: str | None) -> bool:
        normalized_name = normalize(name)
        return bool(self.tokens) and all(token in normalized_name for token in self.tokens)

    def is_exact_name(self, name: str | None) -> bool:
        return tokenize(name) == self.tokens

    def name_rank(self, name: str | None) -> tuple[bool, bool]:
        name_phrase = " ".join(tokenize(name))
        return name_phrase != self.phrase, not name_phrase.startswith(self.phrase)
