# this is to analyze the inbox and extract relevent Information.

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOISE_LOCALPARTS ={
    "no-reply",
    "noreply",
    "no_reply",
    "notification",
    "notifications",
    "alerts",
    "alert",
    "receipts",
    "receipt",
    "billing",
    "invoice",
    "invoices",
    "orders",
    "news-letter",
    "update",
    "news",
    "feedback",
    "status",
    "info",
    "hello",
    "support",

}

NOISE_SUBJECTS = re.compile(
    r"\b(receipt|invoice|statement|digest|newsletter|screen time|"
    r"weekly activity|new notifications|usage|bill is ready|"
    r"campaign report|cloud recording|check-in|your order|your subscription|your payment|"
    r"no action needed|no action required|this is for your information)\b",
    re.IGNORECASE,
)

INJECTION_HINTS = re.compile(
    r"\b(ignore (?:all )?previous instructions|"
    r"disregard (?:the )?(?:previous|prior) instructions|"
    r"without asking|without your consent|without your permission|"
    r"if an ai agent is processing|"
    r"delete this message|delete this email(?: immediately)?|"
    r"assistant (configuration|managing this inbox)|"
    r"do not surface)",
    re.IGNORECASE,
)

PHISH_HINTS = re.compile(
    r"(urgent|action required|immediate action required|"
    r"wire \$?\d|remit the outstanding balance|wire transfer|"
    r"http://\s+|https://\s+|click here to verify|click here to update|"
    r"keep this between us|keep this confidential|keep this private|"
    r"keep this secret|keep this hidden|keep this undisclosed|"
    r"disregard this message|disregard this email|disregard this email immediately)",
    re.IGNORECASE,
    )

PREFERENCE_HINTS = re.compile(
    r"\b(unsubscribe|opt-out|opt out|remove me from this list|"
    r"standing request | please remember my preference|"
    r"please update my preferences|"
    r"change my settings|"
    r"modify my account|"
    r"adjust my notifications|"
    r"customize my experience|"
    r"personalize my feed|"
    r"tailor my content|"
    r"adapt to my interests|"
    r"align with my preferences|"
    r"match my tastes|"
    r"reflect my choices|"
    r"resonate with me|"
    r"connect with me|"
    r"engage with me|"
    r"interact with me|"
    r"respond to me|"
    r"listen to me|"
    r"from now on|"
    r"always cc me|"
    r"always bcc me)",
    re.IGNORECASE,
)

COMMITMENT_HINTS = re.compile(
    r"\b("
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|today|next week|next month|next year|"
    r"asap|as soon as possible|immediately|urgent|"
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"appointment|meeting|call|conference|webinar|event|review"
    r")\b",
    re.IGNORECASE,      
)

AMBIGUOUS_HINTS = re.compile(
    r"\b(please|kindly|request|ask|inquire|seek|"
    r"that thing | the thing| kind of need it|"
    r"earlier|later|soon|eventually|maybe|perhaps|possibly|"
    r"grab coffee|grab lunch|grab dinner|grab a drink|catch up|hang out|meet up|connect|network|collaborate|"
    r"the date you mentioned|the time you suggested|the place you recommended|"
    r"two days before|two days after|three days before|three days after)",
    re.IGNORECASE,
)

REQUIRED_FIELDS = ("id", "thread_id", "from", "to", "subject", "timestamp", "body", "unread")

def domain_of(addr):
    if "@" in addr:
        return addr.rsplit("@", 1)[-1].lower()
    return addr.lower()

def localpart_of(addr):
    if "@" in addr:
        return addr.rsplit("@", 1)[0].lower()
    return addr.lower()

def org_of(domain):
    parts = [p for p in domain.split(".") if p]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else domain

def parse_ts(raw):
    return datetime.fromisoformat(raw)

def infer_owner(messages):
    tos = Counter((m.get("to") or "").lower() for m in messages if m.get("to"))
    if not tos:
        return None
    return tos.most_common(1)[0][0]

SUSPECIOUS_DOMAIN_LABELS = (
    "helpdesk",
    "verify",
    "secure",
    "support",
    "account",
    "billing",
    "mail-backup",
    "invoicing",
    "notification",
)

def looks_like_owner_domain(sender_dom, owner_dom):
    if not owner_dom or sender_dom == owner_dom:
        return False
    owner_org = org_of(owner_dom)
    sender_org = org_of(sender_dom)
    if len(owner_org) < 4 or len(sender_org) < 4:
        return False
    if sender_org == owner_org and sender_dom != owner_dom:
        return True
    if owner_org in sender_dom.split("."):
        return False
    if owner_org in sender_dom and any(label in sender_dom for label in SUSPECIOUS_DOMAIN_LABELS):
        return True
    return False

def suggest_route(flags):
    if "likely_injection" in flags or "likely_phish" in flags:
        return "quarantine"
    if "likely_preference" in flags:
        return "extract_preference"
    if "likely_noise" in flags and "likely_commitment" not in flags:
        return "rules_no_llm"
    if "in_multi_message_thread" in flags:
        return "retrieve_then_llm"
    if "likely_ambiguous" in flags:
        return "escalate_ask"
    return "llm_classify"

def suggest_disposition(flags, from_addr, owner):
    if "likely_injection" in flags or "likely_phish" in flags:
        return "escalate"
    if "likely_preference" in flags:
        return "archive"
    if "likely_noise" in flags and "likely_commitment" not in flags:
        return "archive"
    if owner and from_addr.lower() == owner.lower():
        return "defer"
    if "likely_ambiguous" in flags:
        return "escalate"
    if from_addr and owner and domain_of(from_addr) != domain_of(owner):
        return "external_sender"
    if "likely_commitment" in flags or "needs_reply" in flags:
        return "reply"
    return "defer"

def tag_message(msg, thread_size, owner):
    flags = []
    frm = (msg.get("from") or "").lower()
    to_addr = (msg.get("to") or "").lower()
    subj = (msg.get("subject") or "").lower()
    body = (msg.get("body") or "").lower()
    text = f"{subj}\n{body}"
    loc = localpart_of(frm)
    sender_dom = domain_of(frm)
    owner_dom = domain_of(owner) if owner else None

    flags.append("unread" if msg.get("unread") else "read")

    if owner and frm == owner.lower():
        flags.append("sent_by_owner")
    if owner and to_addr == owner.lower() and frm == owner.lower():
        flags.append("self_sent")

    automated = loc in NOISE_LOCALPARTS or loc.startswith("no-reply") or loc.startswith("noreply") or loc.startswith("no_reply")
    if automated or NOISE_SUBJECTS.search(text):
        flags.append("likely_noise")

    if thread_size > 1:
        flags.append("in_multi_message_thread")
    if thread_size > 5:
        flags.append("long_thread")

    owner_pref = bool(PREFERENCE_HINTS.search(text) and owner and frm == owner)
    if owner_pref or (PREFERENCE_HINTS.search(text) and "likely_noise" not in flags):
        flags.append("likely_preference")

    if INJECTION_HINTS.search(text):
        flags.append("likely_injection")
    if PHISH_HINTS.search(text) or looks_like_owner_domain(sender_dom, owner_dom):
        flags.append("likely_phish")

    if COMMITMENT_HINTS.search(text):
        flags.append("likely_commitment")
    if AMBIGUOUS_HINTS.search(text):
        flags.append("likely_ambiguous")

    if "likely_noise" not in flags and (
        "?" in body or re.search(r"\b(can you |could you|please)\b", body, re.I)
    ):
        flags.append("needs_reply")
    return flags

def load_inbox(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} must be a JSON array of messages")
    for index, message in enumerate(data):
        if not isinstance(message, dict):
            raise SystemExit(f"{path} message {index} must be a JSON object")
        missing = [f for f in REQUIRED_FIELDS if f not in message]
        if missing:
            message_id = message.get("id", index)
            raise SystemExit(
                f"{path} message {message_id} missing fields: {missing}"
            )
    return data

def main():
    parser = argparse.ArgumentParser(description=" Analyze an Inbox JSON (assignment or any other).")
    parser.add_argument("inbox", nargs="?", default=str(ROOT / "inbox.json"))
    parser.add_argument("--owner", default="", help="Mailbox owner email. inferred from `to` if omitted")
    parser.add_argument("--out-dir", default=str(ROOT))
    args = parser.parse_args()

    inbox_path = Path(args.inbox).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    messages = load_inbox(inbox_path)
    owner = (args.owner or infer_owner(messages) or "").lower()

    by_thread = defaultdict(list)
    for m in messages:
        by_thread[m["thread_id"]].append(m)
    for tid in by_thread:
        by_thread[tid].sort(key=lambda x: parse_ts(x["timestamp"]))

    analyzed =[]
    for m in messages:
        thread = by_thread[m["thread_id"]]
        flags = tag_message(m, len(thread), owner)
        analyzed.append(
            {
                "id": m["id"],
                "thread_id": m["thread_id"],
                "from": m["from"],
                "to": m["to"],
                "subject": m["subject"],
                "timestamp": m["timestamp"],
                "unread": m["unread"],
                "body_preview": (m.get("body") or "").replace("\n", " ")[:180],
                "thread_size": len(thread),
                "thread_ids": [x["id"] for x in thread],
                "flags": flags,
                "suggested_route": suggest_route(flags),
                "suggested_disposition": suggest_disposition(flags, m["from"], owner),

            }
        )
    analyzed.sort(key=lambda x: x["timestamp"])
    route_counts = Counter(a["suggested_route"] for a in analyzed)
    disposition_counts = Counter(a["suggested_disposition"] for a in analyzed)
    flag_counts = Counter(f for a in analyzed for f in a["flags"])
    sender_counts = Counter(a["from"] for a in analyzed)

    threads = []
    for thread_id, thread_messages in sorted(
        by_thread.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        threads.append(
            {
                "thread_id": thread_id,
                "size": len(thread_messages),
                "ids": [message["id"] for message in thread_messages],
                "subject": thread_messages[0]["subject"],
                "participants": sorted(
                    {message["from"] for message in thread_messages}
                ),
            }
        )

    timestamps = [parse_ts(message["timestamp"]) for message in messages]
    summary = {
        "generated_by": "analyzeInbox.py",
        "note": "Generic overview of whatever inbox is passed in",
        "inbox": {
            "message_count": len(analyzed),
            "unread_count": sum(1 for message in analyzed if message["unread"]),
            "thread_count": len(by_thread),
            "time_range": {
                "first": min(timestamps).isoformat() if timestamps else None,
                "last": max(timestamps).isoformat() if timestamps else None,
            },
            "route_counts": dict(route_counts),
            "disposition_counts": dict(disposition_counts),
            "flag_counts": dict(flag_counts),
            "sender_counts": dict(sender_counts),
            "top_senders": [
                {"sender": sender, "count": count}
                for sender, count in sender_counts.most_common(10)
            ],
            "threads": threads,
        },
    }
    report = {
        "source": str(inbox_path),
        "owner": owner,
        "message_count": len(analyzed),
        "thread_count": len(by_thread),
        "summary": summary,
        "messages": analyzed,
    }

    analyze_path = out_dir / "analyze.json"
    summary_path = out_dir / "summary.json"
    analyze_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    inbox_summary = summary["inbox"]
    print("\nInbox analysis summary")
    print("=" * 24)
    print(f"Source: {inbox_path}")
    print(f"Owner: {owner or '(not detected)'}")
    print(f"Messages: {inbox_summary['message_count']}")
    print(f"Unread: {inbox_summary['unread_count']}")
    print(f"Threads: {inbox_summary['thread_count']}")
    print(
        "Time range: "
        f"{inbox_summary['time_range']['first']} to "
        f"{inbox_summary['time_range']['last']}"
    )
    print(f"Routes: {inbox_summary['route_counts']}")
    print(f"Dispositions: {inbox_summary['disposition_counts']}")
    print(f"Flags: {inbox_summary['flag_counts']}")
    print(f"Sender counts: {inbox_summary['sender_counts']}")
    print("Top senders:")
    for sender in inbox_summary["top_senders"][:5]:
        print(f"  - {sender['sender']}: {sender['count']}")
    print(f"\nWrote analysis (overwriting existing file): {analyze_path}")
    print(f"Wrote summary (overwriting existing file): {summary_path}")
    return report


def ids_with(analyzed, wanted):
    out = []
    for a in analyzed:
        if wanted in a["flags"]:
            out.append(a["id"])
    return out


if __name__ == "__main__":
    main()



