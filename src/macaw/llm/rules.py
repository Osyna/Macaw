"""Rule-based instant formatter — the zero-dependency "basic" tier.

No model, no download, no venv: a fast pure-Python cleanup that fixes the
mechanical mess dictation leaves — capitalization, spacing, spoken punctuation
and obvious filler — in microseconds, fully offline. It never rewrites, answers
or reshapes text (no emails/lists); for that, use a local or cloud model.

This is a formatter backend like any other (``@register``, ``load``/``format``),
so it shows up in the LLM tab, is always "ready", and needs nothing installed.
"""

from __future__ import annotations

import re

from macaw.llm.base import LlmBackend
from macaw.llm.registry import register

# Spoken punctuation → real punctuation. Order matters: multi-word phrases
# ("new paragraph") must be tried before their single-word prefixes.
_SPOKEN: list[tuple[str, str]] = [
    (r"new paragraph", "\n\n"),
    (r"new line|newline", "\n"),
    (r"full stop|period", "."),
    (r"question mark", "?"),
    (r"exclamation (?:mark|point)", "!"),
    (r"semicolon", ";"),
    (r"colon", ":"),
    (r"open (?:parenthesis|paren)", "("),
    (r"close (?:parenthesis|paren)", ")"),
    (r"comma", ","),
]

# Clear dictation filler only — nothing that can carry meaning ("kind of",
# "sort of" are deliberately left in).
_FILLER = re.compile(r"\b(?:um+|uh+|erm+|you know|i mean)\b", re.IGNORECASE)


def _recase(s: str) -> str:
    """Capitalize sentence starts and the standalone pronoun "i"."""
    s = re.sub(r"\bi\b", "I", s)  # also fixes i'm / i've / i'll (apostrophe = boundary)
    out: list[str] = []
    cap = True  # capitalize the next letter we see
    for ch in s:
        if cap and ch.isalpha():
            ch = ch.upper()
            cap = False
        elif ch in ".!?\n":
            cap = True
        out.append(ch)
    return "".join(out)


def clean(text: str) -> str:
    """Return ``text`` mechanically cleaned. Empty in → empty out."""
    s = (text or "").strip()
    if not s:
        return ""
    for pat, repl in _SPOKEN:
        s = re.sub(rf"\b{pat}\b", repl, s, flags=re.IGNORECASE)
    s = _FILLER.sub("", s)
    # collapse immediate duplicate words ("the the" → "the")
    s = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", s, flags=re.IGNORECASE)
    # spacing around punctuation: none before, one after (but not inside 3.14)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    s = re.sub(r"([,.!?;:])(?=[^\s\d])", r"\1 ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = _recase(s)
    # a single spoken sentence usually wants a full stop it never dictated
    if "\n" not in s and " " in s and s[-1] not in ".!?:,":
        s += "."
    return s.strip()


@register
class RulesBackend(LlmBackend):
    """Instant rule-based cleanup — no model, no download, always available."""

    key = "rules"

    def load(self) -> None:  # nothing to load; instant and in-process
        pass

    def format(self, text: str, system: str) -> str:
        # rules are fixed, so the system prompt does not apply here
        return clean(text)
