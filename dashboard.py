"""Three-pane dashboard from a run. JSON + HTML written together."""

import html
import json
import re
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path

import draft
import gate
import prefs
import rules
import safety

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

ORDINAL = re.compile(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", re.I)
NAMED_DAY = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", re.I)
RELATIVE_BEFORE = re.compile(
    r"(two days before|2 days before)\s+(?:the\s+)?(.+?)(?:[?.!]|$)", re.I
)


def _anchor(messages):
    stamps = []
    for m in messages:
        try:
            stamps.append(datetime.fromisoformat(m["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue

    if stamps:
        return max(stamps)
    return datetime(2026, 9, 1)


def _time_minutes(text):
    hit = TIME_RE.search(text or "")
    if not hit:
        return None

    hour = int(hit.group(1)) % 12
    minute = int(hit.group(2))

    if hit.group(3).lower() == "pm":
        hour += 12

    return hour * 60 + minute


def _fmt_minutes(mins):
    if mins is None:
        return None

    hour, minute = divmod(mins, 60)
    ampm = "am" if hour < 12 else "pm"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{minute:02d}{ampm}"


def _parse_dates(text, anchor):
    dates = []

    for mon, day in NAMED_DAY.findall(text or ""):
        month = MONTHS.get(mon.lower()) or MONTHS.get(mon.lower()[:3])
        d = int(day)
        year = anchor.year

        try:
            dates.append(datetime(year, month, d))
        except ValueError:
            continue

    if dates:
        return dates

    for raw in ORDINAL.findall(text or ""):
        d = int(raw)
        month = anchor.month
        year = anchor.year
        last = monthrange(year, month)[1]

        if d > last:
            if month < 12:
                month += 1
            else:
                month = 1
                year += 1

        try:
            dates.append(datetime(year, month, d))
        except ValueError:
            continue

    return dates


def _looks_like_commitment(text):
    t = (text or "").lower()
    return bool(
        re.search(
            r"\b(deadline|due|by the|scheduled|appointment|review|launch|"
            r"meeting|demo|sign|approve|hold expires|target is)\b",
            t,
        )
    )


def extract_commitments(store):
    anchor = _anchor(store.messages)
    raw = []

    for msg in store.messages:
        if safety.inspect(msg, store.owner):
            continue

        hit = rules.decide(msg, store.owner, store.thread_for(msg))
        if hit and hit["rule_id"] == "noise":
            continue

        text = (msg.get("subject") or "") + " " + (msg.get("body") or "")
        if not _looks_like_commitment(text) and not ORDINAL.search(text) and not NAMED_DAY.search(text):
            continue

        dates = _parse_dates(text, anchor)
        if not dates and RELATIVE_BEFORE.search(text):
            raw.append(
                {
                    "ids": [msg["id"]],
                    "title": (msg.get("subject") or "Commitment").strip(),
                    "date": None,
                    "time": _fmt_minutes(_time_minutes(text)),
                    "when": "relative",
                    "blurb": (msg.get("body") or "")[:180],
                    "relative": RELATIVE_BEFORE.search(text).group(0),
                }
            )
            continue

        if not dates:
            continue

        day = dates[0]
        mins = _time_minutes(text)
        raw.append(
            {
                "ids": [msg["id"]],
                "title": (msg.get("subject") or "Commitment").strip(),
                "date": day.strftime("%Y-%m-%d"),
                "time": _fmt_minutes(mins),
                "when": f"{day.strftime('%Y-%m-%d')} {_fmt_minutes(mins) or ''}".strip(),
                "blurb": (msg.get("body") or "").replace("\n", " ")[:180],
                "relative": None,
            }
        )

    named = [c for c in raw if c["date"] and not c["relative"]]
    relative = [c for c in raw if c["relative"]]
    merged = list(named)

    for rel in relative:
        target = None
        rel_l = (rel["relative"] or "").lower()

        for cand in named:
            title_l = (cand["title"] + " " + cand["blurb"]).lower()

            if "board review" in rel_l and "board" in title_l:
                target = cand
                break

            key = re.sub(r"two days before\s+(?:the\s+)?", "", rel_l)
            if key and key[:12] in title_l:
                target = cand
                break

        if target and target["date"]:
            due = datetime.fromisoformat(target["date"]) - timedelta(days=2)
            ids = list(target["ids"])

            for i in rel["ids"]:
                if i not in ids:
                    ids.append(i)
            ids.sort()

            merged.append(
                {
                    "ids": ids,
                    "title": f"{rel['title']} (two days before {target['title']})",
                    "date": due.strftime("%Y-%m-%d"),
                    "time": target.get("time"),
                    "when": due.strftime("%Y-%m-%d"),
                    "blurb": f"{rel['blurb']} | {target['blurb']}",
                    "relative": None,
                    "multi_message": True,
                }
            )
        else:
            merged.append(rel)

    conflicts = []
    by_slot = {}

    for c in merged:
        if not c.get("date") or not c.get("time"):
            continue

        slot = f"{c['date']} {c['time']}"
        if slot not in by_slot:
            by_slot[slot] = []
        by_slot[slot].append(c)

    for slot, items in by_slot.items():
        ids = []
        for c in items:
            for i in c["ids"]:
                if i not in ids:
                    ids.append(i)
        ids.sort()

        if len(items) >= 2 and len(ids) >= 2:
            titles = [c["title"] for c in items]
            conflicts.append(
                {
                    "slot": slot,
                    "ids": ids,
                    "label": f"CONFLICT: two items at {slot}",
                    "titles": titles,
                }
            )

    return {"items": merged, "conflicts": conflicts}


def build(store, prefs_path=None):
    proposals = gate.collect_proposals(store)
    pending = []

    for p in proposals:
        if p["auto_deny"]:
            continue

        pending.append(
            {
                "message_id": p["message_id"],
                "proposed_action": p["tool"],
                "why_human": "Irreversible send/delete must pass the Part 4 gate (approval or dry-run).",
                "detail": p["reason"],
                "to": p["args"].get("to"),
            }
        )

    pref_data = prefs.load(prefs_path) if prefs_path and prefs_path.exists() else {"preferences": []}
    for act in prefs.apply_inbox(store.messages, pref_data):
        if act.get("action") == "refuse_slot":
            pending.append(
                {
                    "message_id": act["message_id"],
                    "proposed_action": "refuse_or_reschedule",
                    "why_human": "Standing calendar preference blocks this slot.",
                    "detail": act["detail"],
                    "to": None,
                }
            )

    flagged = []
    for f in safety.scan(store.messages, store.owner):
        flagged.append(
            {
                "message_id": f["message_id"],
                "kind": f["kind"],
                "attempted": f["attempted"],
                "instead": "Refused, reported, left in place. No outbox write.",
            }
        )

    for msg in store.messages:
        hit = rules.decide(msg, store.owner, store.thread_for(msg))
        if hit and hit["rule_id"] == "ambiguous":
            flagged.append(
                {
                    "message_id": msg["id"],
                    "kind": "ungrounded",
                    "attempted": "Guess at a vague request",
                    "instead": "Escalate and ask; do not invent a reply.",
                }
            )
            continue

        ask = ((msg.get("subject") or "") + " " + (msg.get("body") or "")).lower()
        wants_fact = False
        for w in ("resend", "the url", "portal link in the previous"):
            if w in ask:
                wants_fact = True

        if wants_fact:
            if draft.draft_reply(store, msg) is None and not safety.inspect(msg, store.owner):
                flagged.append(
                    {
                        "message_id": msg["id"],
                        "kind": "ungrounded",
                        "attempted": "Draft a reply from mailbox facts",
                        "instead": "Information not in the inbox; drafted nothing.",
                    }
                )

    commitments = extract_commitments(store)
    return {
        "owner": store.owner,
        "pending": pending,
        "flagged": flagged,
        "commitments": commitments["items"],
        "conflicts": commitments["conflicts"],
    }


def render_html(data):
    def rows_pending(items):
        if not items:
            return "<tr><td colspan='3'>None</td></tr>"

        out = []
        for i in items:
            out.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(str(i.get("message_id"))),
                    html.escape(str(i.get("proposed_action"))),
                    html.escape(str(i.get("why_human"))),
                )
            )
        return "\n".join(out)

    def rows_flagged(items):
        if not items:
            return "<tr><td colspan='3'>None</td></tr>"

        out = []
        for i in items:
            out.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(str(i.get("message_id"))),
                    html.escape(str(i.get("attempted"))),
                    html.escape(str(i.get("instead"))),
                )
            )
        return "\n".join(out)

    def rows_commit(items, conflicts):
        out = []

        for c in items:
            ids = ", ".join(c.get("ids") or [])
            extra = " [multi-message]" if c.get("multi_message") or len(c.get("ids") or []) > 1 else ""
            out.append(
                "<tr><td>{}{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(ids),
                    extra,
                    html.escape(str(c.get("when") or c.get("date") or "")),
                    html.escape(str(c.get("title") or "")),
                )
            )

        for conf in conflicts:
            out.append(
                "<tr class='conflict'><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(", ".join(conf.get("ids") or [])),
                    html.escape(conf.get("slot") or ""),
                    html.escape(conf.get("label") or "CONFLICT"),
                )
            )

        return "\n".join(out) or "<tr><td colspan='3'>None</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>inboxHero dashboard</title>
  <style>
    body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #222; }}
    h1 {{ font-size: 20px; }}
    h2 {{ font-size: 16px; margin-top: 24px; }}
    .panes {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
    @media (min-width: 900px) {{ .panes {{ grid-template-columns: 1fr 1fr 1fr; }} }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px; vertical-align: top; text-align: left; }}
    th {{ background: #f3f3f3; }}
    tr.conflict td {{ background: #f8e0e0; }}
    .meta {{ color: #555; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>inboxHero dashboard</h1>
  <p class="meta">Owner {html.escape(data.get("owner") or "")}. Generated from the mailbox run, not hand-edited.</p>

  <div class="panes">
    <section>
      <h2>1. Pending actions</h2>
      <table>
        <tr><th>Message</th><th>Proposed</th><th>Why a human</th></tr>
        {rows_pending(data.get("pending") or [])}
      </table>
    </section>

    <section>
      <h2>2. Flagged</h2>
      <table>
        <tr><th>Message</th><th>Attempted</th><th>Instead</th></tr>
        {rows_flagged(data.get("flagged") or [])}
      </table>
    </section>

    <section>
      <h2>3. Commitments</h2>
      <table>
        <tr><th>Cited ids</th><th>When</th><th>What</th></tr>
        {rows_commit(data.get("commitments") or [], data.get("conflicts") or [])}
      </table>
    </section>
  </div>
</body>
</html>
"""


def write(data, json_path, html_path):
    json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(data), encoding="utf-8")
