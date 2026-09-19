"""Build a reply only from retrieved messages. Never send. Never invent."""

 

import retrieve

import safety

 

def _usable_facts(hits, target):

    """Prefer URLs already in earlier mail when the ask is to resend a link."""

    ask = ((target.get("subject") or "") + " " + (target.get("body") or "")).lower()

    wants_url = False

    for w in ("url", "link", "creds", "credential", "amqp", "resend", "portal"):
        if w in ask:
            wants_url = True
            break

    facts = []

    for hit in hits:

        snippets = hit.get("snippets") or []

        if hit.get("method") != "thread-walk" and wants_url and not snippets:

            continue

        for snip in snippets:

            facts.append((hit, snip))

        if not snippets and hit.get("method") == "thread-walk" and not wants_url:

            body = (hit["message"].get("body") or "").strip()

            if body:

                facts.append((hit, body.split("\n")[0][:240]))

    return facts

 

def draft_reply(store, target):
    finding = safety.inspect(target, store.owner)
    if finding:
        return None

    hits = retrieve.retrieve(store, target)

    facts = _usable_facts(hits, target)

    copied = []
    for hit, snippet in facts:
        body = hit["message"].get("body") or ""
        if snippet in body:
            copied.append((hit, snippet))

    if not copied:
        return None

    cited = []
    methods = []
    lines = [
        "Hi,",
        "",
        "This draft uses only facts already in the mailbox.",
        "",
    ]

    for hit, snippet in copied:
        mid = hit["message"]["id"]
        if mid not in cited:
            cited.append(mid)
            methods.append(hit["method"])
        lines.append(f"From {mid}: {snippet}")

    lines.extend(["", "Happy to adjust if you meant a different thread.", ""])

    subj = target.get("subject") or ""
    if subj.lower().startswith("re:"):
        re_subj = subj
    else:
        re_subj = "Re: " + subj

    return {
        "target_id": target["id"],
        "to": target.get("from") or "",
        "subject": re_subj,
        "body": "\n".join(lines),
        "cited": cited,
        "method": methods[0] if methods else "thread-walk",
    }