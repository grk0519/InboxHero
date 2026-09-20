"""Find earlier mail that a reply can stand on. Thread-walk first, then keyword."""

import re

URL_RE = re.compile(r"(https?://[^\s>]+|amqp://[^\s>]+)", re.I)
STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "you", "your",
    "can", "just", "here", "this", "that", "with", "from", "have", "has", "was",
    "are", "i", "i'm", "im", "don't", "dont", "need", "want", "please",
}


def tokens(text):
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    keep = []

    for w in words:
        if w not in STOP and w not in keep:
            keep.append(w)

    return keep


def extract_snippets(body):
    found = []
    for match in URL_RE.findall(body or ""):
        found.append(match.rstrip(").,;"))
    return found


def _score(query, msg):
    blob = (msg.get("subject") or "") + " " + (msg.get("body") or "")
    other = tokens(blob)

    n = 0
    for w in query:
        if w in other:
            n += 1

    return n


def retrieve(store, msg, limit=6):
    """Return earlier messages the draft may cite. Does not invent content."""
    hits = []
    seen = set()

    for earlier in store.earlier_in_thread(msg):
        seen.add(earlier["id"])
        hits.append(
            {
                "message": earlier,
                "method": "thread-walk",
                "snippets": extract_snippets(earlier.get("body") or ""),
            }
        )

    query = tokens((msg.get("subject") or "") + " " + (msg.get("body") or ""))
    ranked = []

    for other in store.messages:
        if other["id"] == msg["id"] or other["id"] in seen:
            continue

        score = _score(query, other)
        if score >= 2:
            ranked.append((score, other))

    ranked.sort(key=lambda item: item[0], reverse=True)

    for _, other in ranked[:limit]:
        hits.append(
            {
                "message": other,
                "method": "keyword",
                "snippets": extract_snippets(other.get("body") or ""),
            }
        )

    return hits
