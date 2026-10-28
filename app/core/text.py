"""Unicode normalisation and tokenisation for multilingual search.

PostgreSQL has stemmers for Russian and English but nothing for Turkmen and no
word segmentation for Chinese. Instead of a custom C extension the text is
prepared in Python and stored in ``thoughts.search_document``; a generated
``tsvector`` column indexes it with the ``simple`` (exact forms, any script)
and ``russian`` (Russian stems + English stems for ASCII words) configs.

* NFKC + casefold: full-width forms, ligatures, case.
* Turkmen/Latin fold: a diacritic-free copy (``ýyldyrym`` -> ``yyldyrym``) is
  appended, so a query typed without diacritics still matches.
* CJK: runs of Han/Kana/Hangul become unigrams + overlapping bigrams, the same
  idea as Lucene's CJKAnalyzer; the query is expanded the same way.
"""

from __future__ import annotations

import re
import unicodedata

_CJK_RANGES = (
    ("぀", "ヿ"),  # Hiragana, Katakana
    ("㐀", "䶿"),  # CJK Extension A
    ("一", "鿿"),  # CJK Unified Ideographs
    ("가", "힯"),  # Hangul syllables
    ("豈", "﫿"),  # CJK Compatibility Ideographs
)
_CJK_RUN = re.compile("[" + "".join(f"{a}-{b}" for a, b in _CJK_RANGES) + "]+")
_HASHTAG = re.compile(r"(?<![\w#])#(\w[\w\-]{0,63})")
_WS = re.compile(r"\s+")
_TSQUERY_SPECIAL = re.compile(r"[&|!():*<>\\]")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def is_cjk(ch: str) -> bool:
    return any(a <= ch <= b for a, b in _CJK_RANGES)


def fold_latin(text: str) -> str:
    """Strip diacritics from Latin letters only (Cyrillic й/ё stay intact)."""
    out: list[str] = []
    for ch in text:
        if "a" <= ch <= "z" or ch < "À" or not ch.isalpha():
            out.append(ch)
            continue
        decomposed = unicodedata.normalize("NFD", ch)
        base = decomposed[0]
        if "a" <= base <= "z":
            out.append(base)
        elif ch == "ı":  # dotless i
            out.append("i")
        else:
            out.append(ch)
    return "".join(out).replace("ё", "е")


def _expand_cjk_run(run: str, *, for_query: bool) -> str:
    if len(run) == 1:
        return run
    bigrams = [run[i : i + 2] for i in range(len(run) - 1)]
    if for_query:
        return " ".join(bigrams)
    return " ".join(list(run) + bigrams)


def expand_cjk(text: str, *, for_query: bool = False) -> str:
    return _CJK_RUN.sub(lambda m: f" {_expand_cjk_run(m.group(0), for_query=for_query)} ", text)


def build_search_document(*parts: str | None) -> str:
    text = "\n".join(p for p in parts if p)
    norm = normalize(text)
    doc = expand_cjk(norm)
    folded = fold_latin(norm)
    if folded != norm:
        doc = f"{doc}\n{expand_cjk(folded)}"
    return doc


def prepare_query(query: str) -> tuple[str, str]:
    """Return (normalized, folded) query strings for ``websearch_to_tsquery``.

    websearch syntax (quotes for phrases, ``or``, ``-word``) is kept; characters
    that only mean something in raw tsquery syntax are removed.
    """
    norm = _TSQUERY_SPECIAL.sub(" ", normalize(query))
    norm = _WS.sub(" ", expand_cjk(norm, for_query=True)).strip()
    folded = fold_latin(norm)
    return norm, folded


def extract_hashtags(content: str) -> list[str]:
    seen: dict[str, str] = {}
    for match in _HASHTAG.finditer(content):
        raw = match.group(1).rstrip("-")
        key = normalize_tag(raw)
        if key and key not in seen:
            seen[key] = raw
    return list(seen.values())


def normalize_tag(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip().lstrip("#").strip()
    return _WS.sub(" ", name).casefold()


def display_tag(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip().lstrip("#").strip()
    return _WS.sub(" ", name)


def query_terms(query: str) -> list[str]:
    norm, _ = prepare_query(query.replace('"', " "))
    return [t for t in norm.split(" ") if t and t not in {"or", "and"} and not t.startswith("-")]


def snippet(content: str, query: str, width: int = 240) -> str:
    """A window of the original text around the first query term."""
    if len(content) <= width:
        return content
    haystack = fold_latin(normalize(content))
    best = -1
    for term in query_terms(query):
        pos = haystack.find(fold_latin(term))
        if pos != -1 and (best == -1 or pos < best):
            best = pos
    if best == -1:
        return content[:width].rstrip() + "…"
    start = max(0, best - width // 3)
    end = min(len(content), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return prefix + content[start:end].strip() + suffix


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
