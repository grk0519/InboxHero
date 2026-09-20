"""Cheap routing with if/else. No language model."""

import re
import safety

NOISE_LOCALPARTS = {
    "no-reply",
    "noreply",
    "no_reply",
    "notifications",
    "notification",
    "alerts",
    "alert",
    "receipts",
    "receipt",
    "billing",
    "invoice",
    "invoices",
    "orders",
    "ship-confirm",
    "digest",
    "newsletter",
    "updates",
    "insights",
    "feedback",
    "mailer-daemon",
    "calendar-notification",
    "checkin",
    "status",
    "info",
    "hello",
}

PREFERENCE_HINTS = re.compile(
    r"(standing request|please remember|"
    r"note for the assistant|"
    r"i do not take meetings|"
    r"never agree to|"
    r"always cc)",
    re.I,
)

AMBIGUOUS_HINTS = re.compile(
    r"\b(that thing|the thing|kind of need it|"
    r"when you(?:'re| are) back|no agenda|"
    r"grab coffee|catch up)\b",
    re.I,
)


def localpart_of(addr):
    addr = addr or ""

    if "@" in addr:
        return addr.split("@", 1)[0].lower()

    return addr.lower()


def _text(msg):
    return (msg.get("subject") or "") + "\n" + (msg.get("body") or "")


def _hit(disposition, reason, rule_id):
    return {
        "disposition": disposition,
        "reason": reason,
        "rule_id": rule_id,
        "via": "rules",
    }


def decide(msg, owner, thread):
    """Return a dict if a rule is confident, else None (LLM / stub)."""
    frm = (msg.get("from") or "").lower()
    owner = (owner or "").lower()
    text = _text(msg)
    loc = localpart_of(frm)
    thread_size = len(thread or [])

    finding = safety.inspect(msg, owner)

    if finding and finding["kind"] == "injection":
        return _hit("escalate", finding["attempted"], "injection")
    if finding and finding["kind"] == "phishing":
        return _hit("escalate", finding["attempted"], "phishing")

    if PREFERENCE_HINTS.search(text) and frm == owner:
        return _hit(
            "archive",
            "Owner standing instruction; record as a preference, do not treat as a task.",
            "owner_preference",
        )

    if frm == owner:
        return _hit(
            "defer",
            "Sent by the mailbox owner; nothing to reply to unless a follow-up is requested.",
            "owner_sent",
        )

    automated = loc in NOISE_LOCALPARTS or loc.startswith("no-reply") or loc.startswith("noreply")

    # Do not key off words like "invoice" in the body — humans use those too.
    if automated and thread_size <= 1:
        return _hit(
            "archive",
            "Automated noise (receipt, newsletter, or no-reply alert).",
            "noise",
        )

    if AMBIGUOUS_HINTS.search(text) and frm != owner:
        return _hit(
            "escalate",
            "Request is too vague to answer without guessing.",
            "ambiguous",
        )

    return None