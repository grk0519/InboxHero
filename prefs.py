"""Standing instructions on disk. Survive a full process exit."""

 

import json

import re

from pathlib import Path

 

import safety

from store import domain_of

 

CC_HINT = re.compile(r"(cc['’]?d|cc me|loop me in|copy me|always cc)", re.I)

BEFORE_TIME = re.compile(r"before\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)

PROPOSED_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)

 

def _text(msg):

    return (msg.get("subject") or "") + "\n" + (msg.get("body") or "")

 

def _to_minutes(hour, minute, ampm):

    hour = hour % 12

    if ampm.lower() == "pm":

        hour += 12

    return hour * 60 + minute

 

def extract_from_message(msg, owner):

    """Parse a preference out of one message. Skip hostile mail."""

    if safety.inspect(msg, owner):

        return []

    text = _text(msg)

    frm = (msg.get("from") or "").lower()

    prefs = []

    before = BEFORE_TIME.search(text)
    looks_calendar = (
        "meeting" in text.lower()
        or "please remember" in text.lower()
        or frm == (owner or "").lower()
    )

    if before and looks_calendar:
        hh = int(before.group(1))
        mm = int(before.group(2) or "0")
        ampm = before.group(3)

        prefs.append(
            {
                "type": "no_meetings_before",
                "minutes": _to_minutes(hh, mm, ampm),
                "label": f"{hh}:{mm:02d}{ampm.lower()}",
                "source_id": msg["id"],
            }
        )

    standing = CC_HINT.search(text) and re.search(r"standing|from now on|always", text, re.I)

    if standing:

        org = ""

        org_match = re.search(r"from (?:our )?lawyers at ([^.\n]+)", text, re.I)

        if not org_match:

            org_match = re.search(r"from ([A-Z][\w&.\- ]+)", text)

        if org_match:

            org = re.sub(r"[^a-z0-9]+", "", org_match.group(1).lower())

        prefs.append(

            {

                "type": "cc_on_match",

                "cc": frm,

                "match": org or domain_of(frm).split(".")[0],

                "source_id": msg["id"],

            }

        )

    return prefs

 

def extract_all(messages, owner):

    items = []

    for msg in messages:

        items.extend(extract_from_message(msg, owner))

    return {"owner": owner, "preferences": items}

 

def save(path, data):

    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

 

def load(path):

    path = Path(path)

    if not path.exists():

        return {"owner": "", "preferences": []}

    return json.loads(path.read_text(encoding="utf-8"))

 

def _matches_cc(msg, rule):

    needle = (rule.get("match") or "").lower()

    if not needle:

        return False

    blob = f"{msg.get('from') or ''} {msg.get('subject') or ''} {_text(msg)}".lower()

    compact = re.sub(r"[^a-z0-9]+", "", blob)

    return needle in blob or needle in compact

 

def _proposed_start_minutes(msg):

    times = PROPOSED_TIME.findall(_text(msg))

    if not times:

        return None

    hour, minute, ampm = times[0]

    return _to_minutes(int(hour), int(minute or "0"), ampm)

 

def apply_to_message(msg, data):

    """How saved prefs change treatment of this message."""

    actions = []

    for rule in data.get("preferences") or []:

        if rule.get("source_id") == msg.get("id"):

            continue

        if rule.get("type") == "cc_on_match" and _matches_cc(msg, rule):

            actions.append(

                {

                    "message_id": msg["id"],

                    "action": "cc",

                    "cc": rule.get("cc"),

                    "rule_source": rule.get("source_id"),

                    "detail": f"CC {rule.get('cc')} (preference {rule.get('source_id')})",

                }

            )

        if rule.get("type") == "no_meetings_before":

            blob = _text(msg).lower() + " " + (msg.get("subject") or "").lower()

            if not re.search(r"\b(meeting|call|slot|demo|1:1|coffee)\b", blob):

                continue

            start = _proposed_start_minutes(msg)

            limit = int(rule.get("minutes") or 0)

            if start is not None and start < limit:

                actions.append(

                    {

                        "message_id": msg["id"],

                        "action": "refuse_slot",

                        "rule_source": rule.get("source_id"),

                        "detail": (

                            f"Proposed time is before {rule.get('label')}; "

                            f"do not accept; offer {rule.get('label')} or later "

                            f"(preference {rule.get('source_id')})"

                        ),

                    }

                )

    return actions

 

def apply_inbox(messages, data):

    out = []

    for msg in messages:

        out.extend(apply_to_message(msg, data))

    return out