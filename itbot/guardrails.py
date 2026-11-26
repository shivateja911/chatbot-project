"""Checks that run in code, so they hold whatever the model does.

Pattern matching is a safety net, not full data-loss prevention.
"""
import re
from urllib.parse import urlparse

ALLOWED_LINK_DOMAIN = "lumenharbor.example"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Return the text with likely secrets replaced, plus the kinds found."""
    found: set[str] = set()

    def mark(kind):
        found.add(kind)
        return f"[REDACTED {kind}]"

    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{16,}", lambda m: mark("API key"), text)
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", lambda m: mark("SSN"), text)
    text = re.sub(
        r"\b(?:\d[ -]?){12,18}\d\b",
        lambda m: mark("card number") if _luhn_ok(re.sub(r"\D", "", m[0])) else m[0], text)

    def password(m):
        # Only redact values that look like a secret, so "my password is expired" survives.
        value = m["value"].rstrip(".,;")
        if len(value) >= 6 and re.search(r"[^A-Za-z]", value):
            return m["lead"] + mark("password") + m["value"][len(value):]
        return m[0]

    text = re.sub(
        r"(?P<lead>\b(?:password|passwd|pwd|passcode)\b(?:\s+is\s+|\s*[:=]\s*))(?P<value>\S+)",
        password, text, flags=re.I)
    return text, sorted(found)


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == ALLOWED_LINK_DOMAIN or host.endswith("." + ALLOWED_LINK_DOMAIN)


def sanitize_output(text: str) -> str:
    """Remove images and any link that leaves the company domain."""
    text = re.sub(r"!\[[^\]]*\](\([^)]*\)|\[[^\]]*\])", "[image removed]", text)
    text = re.sub(r"<[^>\n]*>", lambda m: "[link removed]" if "://" in m[0] else m[0], text)
    return re.sub(r"https?://[^\s)\]>\"'`]+",
                  lambda m: m[0] if _allowed(m[0].rstrip(".,;:")) else "[link removed]",
                  text, flags=re.I)


def unknown_citations(reply: str, retrieved: set[str]) -> list[str]:
    """Article IDs the reply cites without having retrieved them."""
    return sorted(set(re.findall(r"\bKB-\d{3}\b", reply)) - retrieved)
