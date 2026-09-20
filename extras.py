"""Part 8 extras: X1-X4."""

 

import re

from datetime import datetime

 

import rules

import safety

 

ASK_OWNER = re.compile(

    r"\b(can you|could you|please (approve|review|confirm|sign)|needs sam|sam,)\b",

    re.I,

)

CLOSED = re.compile(

    r"\b(approved|fixed it|green|done|thanks\.|uploading final)\b",

    re.I,

)

LAUNCH_DATE = re.compile(

    r"\b(?:target is |hard date[, ]*)(?:the )?(\d{1,2}(?:st|nd|rd|th))\b",

    re.I,

)

 

def _now(store):

    stamps = []

    for m in store.messages:

        try:

            stamps.append(datetime.fromisoformat(m["timestamp"]))

        except (TypeError, ValueError, KeyError):

            continue

    if stamps:

        return max(stamps)

    return datetime.now()

 

def unread_from(store, sender):

    want = (sender or "").lower()

    rows = []

    for m in store.messages:

        if not m.get("unread"):

            continue

        frm = (m.get("from") or "").lower()

        if frm != want:

            continue

        if frm == store.owner:

            continue

        rows.append(

            {

                "id": m["id"],

                "subject": m.get("subject"),

                "timestamp": m.get("timestamp"),

            }

        )

    return rows

 

def followups(store):

    now = _now(store)

    out = []

    for m in store.messages:

        if (m.get("from") or "").lower() != store.owner:

            continue

        if (m.get("to") or "").lower() == store.owner:

            continue

        if safety.inspect(m, store.owner):

            continue

        later_inbound = False

        for x in store.thread_for(m):

            if x["id"] == m["id"]:

                continue

            if (x.get("timestamp") or "") <= (m.get("timestamp") or ""):

                continue

            if (x.get("from") or "").lower() != store.owner:

                later_inbound = True

                break

        if later_inbound:

            continue

        sent = datetime.fromisoformat(m["timestamp"])

        days = max(0, (now - sent).days)

        to_addr = m.get("to") or ""

        subj = m.get("subject") or ""

        out.append(

            {

                "message_id": m["id"],

                "days_waiting": days,

                "draft": (

                    f"Hi, circling back on {subj}. "

                    f"Sent {days} day(s) ago to {to_addr}. "

                    "Any update when you have a moment?"

                ),

            }

        )

    return out

 

def digest(store):

    needs = []

    wait = []

    archived = 0

    for m in store.messages:

        finding = safety.inspect(m, store.owner)

        hit = rules.decide(m, store.owner, store.thread_for(m))

        if hit and hit["rule_id"] == "noise":

            archived += 1

            continue

        if finding:

            continue

        if hit and hit["rule_id"] in {"owner_sent", "owner_preference"}:

            wait.append({"id": m["id"], "why": "owner-sent or preference note"})

            continue

        frm = (m.get("from") or "").lower()

        text = ((m.get("subject") or "") + " " + (m.get("body") or "")).lower()

        legal = "hartwell" in frm or "hartwell" in text

        investor = "northwind" in frm or "intro call" in text or ("partner" in text and "vc" in frm)

        launch_ask = "pricing" in text and "approve" in text

        if legal or investor or launch_ask:

            needs.append({"id": m["id"], "why": "legal / investor / launch decision"})

            continue

        if hit and hit["rule_id"] == "ambiguous":

            wait.append({"id": m["id"], "why": "too vague to act"})

            continue

        if ASK_OWNER.search(text) and m.get("unread"):

            needs.append({"id": m["id"], "why": "asks the owner for something"})

            continue

        if m.get("unread") and not (hit and hit["disposition"] == "archive"):

            wait.append({"id": m["id"], "why": "FYI / can wait"})

    return {

        "needs_you": needs,

        "can_wait": wait,

        "auto_archived": {

            "count": archived,

            "note": "receipts/newsletters/no-reply, listed by count only",

        },

    }

 

def thread_open_question(store, thread_id):

    items = store.thread(thread_id)

    if not items:

        return None

    target = None

    for m in items:

        text = (m.get("subject") or "") + " " + (m.get("body") or "")

        hit = LAUNCH_DATE.search(text)

        if hit:

            target = "the " + hit.group(1)

 

    open_asks = []

    for m in items:

        body = m.get("body") or ""

        if (m.get("from") or "").lower() == store.owner:

            continue

        if not ASK_OWNER.search(body) and "needs sam" not in body.lower() and "sam specifically" not in body.lower():

            continue

        later_closed = False

        for x in items:

            if (x.get("timestamp") or "") <= (m.get("timestamp") or ""):

                continue

            if CLOSED.search(x.get("body") or ""):

                later_closed = True

                break

        pricing_or_sam = (

            "pricing" in body.lower()

            or "sam specifically" in body.lower()

            or "can you approve" in body.lower()

        )

        if pricing_or_sam:

            open_asks.append({"id": m["id"], "ask": body.strip().split("\n")[0][:280]})

            continue

        if not later_closed:

            open_asks.append({"id": m["id"], "ask": body.strip()[:280]})

 

    closed_ids = []

    for m in items:

        if CLOSED.search(m.get("body") or ""):

            closed_ids.append(m["id"])

 

    return {

        "thread_id": thread_id,

        "size": len(items),

        "ids": [m["id"] for m in items],

        "launch_target": target,

        "open_question": open_asks[0] if open_asks else None,

        "not_the_open_question": closed_ids,

    }