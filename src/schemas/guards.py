"""AgentCore Platform v1.0"""

# CMN-C1-068 — Caller-input guards (pure, stateless, no framework dependency).
#
# Every value in this template's output document originates in a caller-supplied
# JSON payload. These helpers are the single place that decides what a caller may
# put into the rendered document, and they exist because of three measured
# properties of the generated artifact:
#
#   1. STRUCTURE IS SEMANTICS. The document is plain text whose meaning is carried
#      by line structure — numbered section headers, "=" rules, and label lines
#      such as "Human Governance Review:  RECOMMENDED". A free-text field that is
#      allowed to contain a newline can therefore manufacture an entire extra
#      section, including a governance verdict the agent never assessed.
#      `neutralise_text()` removes that capability at the source: caller prose is
#      rendered as a single line, and runs of rule characters are collapsed, so no
#      caller string can look like document structure.
#
#   2. IDENTIFIER-SHAPED VALUES MUST STAY INERT. Framework ids and metric names are
#      rendered into label lines verbatim. They are enum-like by nature, so they are
#      locked to an inert character class rather than neutralised as prose.
#
#   3. NON-FINITE NUMBERS FAIL OPEN. Python's json module accepts the non-standard
#      `NaN` / `Infinity` / `-Infinity` literals, and `float("1e999")` is `inf`.
#      Either one renders into a compliance document as "nan" / "inf" under a
#      success status. `reject_json_constant()` and `finite_number()` make that
#      fail closed instead.
#
# A fourth guard, `screen_control_tokens()`, covers a class the platform input
# screen does not: chat-template control markers. The platform screen scores
# `<|im_start|>`, `[INST]` and `<system>` as high-confidence findings but returns
# nothing at all for `<<SYS>>`, so a directive wrapped in that marker reaches the
# document intact. The screen here treats the marker family as one class, checks
# both the raw string and the markup-stripped string (a strip that removes a
# marker can re-assemble a spliced directive), and walks dict keys as well as
# values — a payload key is caller data too.
#
# Findings name a LOCATION, never the matched text.

import json
import math
import re
import unicodedata
from typing import Any, Optional, Tuple

# The platform input filter replaces personal-data-shaped spans with this
# sentinel BEFORE any template code runs. It arrives as an ordinary string, so a
# template that does not look for it will happily present it as a caller-declared
# value — see `contains_redaction_sentinel`.
REDACTION_SENTINEL = "[MASKED]"

# Characters that carry document structure in the rendered governance document.
_RULE_CHARS = "=-_*#~"

# Runs of 3+ rule characters read as a horizontal rule; a single one does not.
_RULE_RUN_RE = re.compile(rf"([{re.escape(_RULE_CHARS)}])\1{{2,}}")

# Tidies runs of literal SPACES only. It deliberately does not match `\s`: a
# `\s+` pass here would also erase newlines, which would silently do the job of
# the single-pass flattening below and make that guard unfalsifiable — removing
# the guard would leave every structural test still passing. One guard, one job.
_SPACE_RUN_RE = re.compile(r" {2,}")

# Markup strip used for the second pass of the control-token screen.
_TAG_RE = re.compile(r"<[^>]{0,120}>")

# Chat-template control markers, screened as ONE class.
# `<<SYS>>` is the member the platform screen does not score at all.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|[^|>]{0,40}\|>"  # <|im_start|>, <|im_end|>, <|system|>
    r"|<</?\s*SYS\s*>>"  # <<SYS>>, <</SYS>>
    r"|\[/?\s*(?:INST|SYS)\s*\]"  # [INST], [/INST], [SYS]
    r"|<\s*/?\s*(?:system|user|assistant)\s*>"  # <system>, </assistant>
    r"|\#{3,}\s*(?:system|instruction)\b",  # ### system
    re.IGNORECASE,
)

# Directive phrases screened on the MARKUP-STRIPPED text, so a spliced form
# ("ig<b>nore all previous instructions") is caught once the strip re-assembles it.
_DIRECTIVE_RE = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|the\s+above)\s+instructions"
    r"|disregard\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules)"
    r"|you\s+are\s+now\s+(?:a|an|the)\b"
    r"|new\s+system\s+prompt"
    r"|override\s+(?:your|the)\s+(?:instructions|rules|system\s+prompt)",
    re.IGNORECASE,
)

# A field name is caller data too: echo it only when it is inert.
_SAFE_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,64}$")

# Inert class for values rendered into label lines (framework ids, metric names).
_INERT_STRIP_RE = re.compile(r"[^a-z0-9_]+")
_INERT_METRIC_STRIP_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


class NonFiniteJSONConstant(ValueError):
    """Raised when a payload uses the non-standard NaN / Infinity JSON literals."""


def reject_json_constant(name: str) -> Any:
    """`json.loads(..., parse_constant=...)` hook — refuse NaN / Infinity literals.

    Python's json module accepts these by default. They are not valid JSON, and a
    governance document that renders "nan" as a reported metric under a success
    status is worse than a refusal.
    """
    raise NonFiniteJSONConstant(f"non-finite JSON constant is not accepted: {name}")


def loads_strict(raw: str) -> Any:
    """`json.loads` with the non-finite literal extension disabled."""
    return json.loads(raw, parse_constant=reject_json_constant)


def finite_number(value: Any) -> Optional[float]:
    """Return ``value`` as a finite float, or None if it is not one.

    Handles the three shapes a caller can send a number in: a JSON number, a
    numeric string ("0.87"), and a numeric string that overflows to infinity
    ("1e999"). Booleans are rejected — ``isinstance(True, int)`` is True in
    Python, and a boolean is not a measurement.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
    elif isinstance(value, str):
        try:
            candidate = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    return candidate if math.isfinite(candidate) else None


def neutralise_text(value: Any, max_chars: int) -> str:
    """Render caller prose so it cannot impersonate document structure.

    Three properties:
      * ONE pass flattens the text: every whitespace character — ``str.isspace()``
        covers ``\\n``, ``\\r``, ``\\x85``, ``U+2028`` and ``U+2029`` — becomes a
        single space, and every other control character is dropped. It is one pass
        on purpose. Splitting it into a line-break substitution and a separate
        control-character filter gave two guards that each removed newlines on
        their own, so deleting either left every structural test passing and the
        remaining guard unprovable;
      * a run of three or more rule characters (``= - _ * #``) collapses to one,
        so a horizontal rule cannot be forged;
      * the result is truncated to ``max_chars`` with a visible marker, so a
        single field cannot dominate the document.

    The consequence of the first property is the one that matters: caller text can
    never start a line, so it can never look like a section header or a label line.
    """
    text = "" if value is None else str(value)
    text = "".join(" " if ch.isspace() else ch for ch in text if ch.isspace() or unicodedata.category(ch)[0] != "C")
    text = _RULE_RUN_RE.sub(r"\1", text)
    text = _SPACE_RUN_RE.sub(" ", text).strip()
    limit = max(1, int(max_chars))
    if len(text) > limit:
        text = text[:limit].rstrip() + " […truncated]"
    return text


def inert_identifier(value: Any, max_chars: int = 32) -> str:
    """Lock a caller value to ``[a-z0-9_]`` — used for values rendered as labels."""
    text = _INERT_STRIP_RE.sub("_", str(value).strip().lower()).strip("_")
    return text[: max(1, int(max_chars))]


def inert_metric_name(value: Any, max_chars: int = 40) -> str:
    """Lock a metric name to ``[A-Za-z0-9_.-]`` (case and dots are meaningful here)."""
    text = _INERT_METRIC_STRIP_RE.sub("_", str(value).strip()).strip("_")
    return text[: max(1, int(max_chars))]


def safe_field_label(path: str, index: int) -> str:
    """Name a field in an error message without echoing caller-controlled text."""
    return path if _SAFE_FIELD_NAME_RE.match(path) else f"payload field #{index}"


def contains_redaction_sentinel(value: Any) -> bool:
    """True when the platform input filter has already redacted part of this value."""
    return REDACTION_SENTINEL in str(value)


def screen_control_tokens(payload: Any) -> Optional[Tuple[str, str]]:
    """Depth-first screen for chat-template control markers and directive phrases.

    Walks dict KEYS as well as values (a key is caller data too) and every list
    item. Each string is checked twice — raw, which catches a marker before a
    markup strip could remove it, and markup-stripped, which catches a directive
    that was spliced across tags.

    Returns ``(finding_name, field_label)`` for the first match, or None.
    The matched text is never returned.
    """
    counter = [0]

    def walk(node: Any, path: str) -> Optional[Tuple[str, str]]:
        if isinstance(node, dict):
            for key, sub in node.items():
                counter[0] += 1
                hit = check(str(key), f"{path}<key>" if path else "<key>")
                if hit:
                    return hit
                child = f"{path}.{key}" if path else str(key)
                hit = walk(sub, child)
                if hit:
                    return hit
            return None
        if isinstance(node, (list, tuple)):
            for i, sub in enumerate(node):
                hit = walk(sub, f"{path}[{i}]")
                if hit:
                    return hit
            return None
        if isinstance(node, str):
            counter[0] += 1
            return check(node, path or "payload")
        return None

    def check(text: str, path: str) -> Optional[Tuple[str, str]]:
        label = safe_field_label(path, counter[0])
        if _CONTROL_TOKEN_RE.search(text):
            return ("chat_template_control_token", label)
        stripped = _TAG_RE.sub("", text)
        if _CONTROL_TOKEN_RE.search(stripped):
            return ("chat_template_control_token", label)
        if _DIRECTIVE_RE.search(text) or _DIRECTIVE_RE.search(stripped):
            return ("instruction_override_directive", label)
        return None

    return walk(payload, "")
