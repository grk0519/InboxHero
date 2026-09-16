"""
analyze.py — inbox recon + dynamic summary + on-demand manifest builder.

Usage:
    python analyze.py [inbox.json]                 # recon; refreshes analysis_summary.json; appends trace.jsonl
    python analyze.py [inbox.json] --manifest      # also (re)generates manifest.md from manifest_template.md

File lifetimes:
    inbox.json            -> input; read fresh on every run
    analysis_summary.json -> OVERWRITTEN each run (fixed shape, fixed size — never grows)
    trace.jsonl           -> APPENDED each run, one compact line each, capped at MAX_TRACE_LINES
    manifest_template.md  -> YOUR static policy prose + {{placeholders}} (edit this, never manifest.md)
    manifest.md           -> generated from template + summary data; do NOT hand-edit
"""
import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

INBOX, SUMMARY, TRACE_LOG = "inbox.json", "analysis_summary.json", "trace.jsonl"
TEMPLATE, MANIFEST = "manifest_template.md", "manifest.md"
REQUIRED_KEYS = ["id", "thread_id", "from", "to", "subject", "timestamp", "body", "unread"]
LONG_THREAD = 3          # threads with >= this many messages need full-thread reading
BODY_PREVIEW = 300
MAX_TRACE_LINES = 500    # bound trace.jsonl growth
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_inbox(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        sys.exit(f"Expected a JSON list of messages, got {type(data).__name__}")
    return data


def to_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v)


def validate_schema(messages):
    anomalies, unread_problems = [], []
    for m in messages:
        mid = m.get("id", "?")
        for key in REQUIRED_KEYS:
            if key not in m:
                anomalies.append(f"[{mid}] missing key: {key}")
        if "unread" in m and not isinstance(m["unread"], bool):
            unread_problems.append(f"{mid}={m['unread']!r}")
    return anomalies, unread_problems


def timestamp_note(messages):
    all_str = all("timestamp" in m and isinstance(m["timestamp"], str) for m in messages)
    sample = next((m["timestamp"] for m in messages if "timestamp" in m), "")
    if all_str and ISO_RE.match(sample):
        return f"ISO-8601 strings (sample: {sample!r})"
    non_bool = next((m["timestamp"] for m in messages if not isinstance(m.get("timestamp"), (int, str))), None)
    if non_bool is not None:
        return f"UNEXPECTED timestamp type: {type(non_bool).__name__} — resolve in manifest"
    return "mixed/other (verify by hand)"


def build_flags(m, thread_size):
    sender, subject, body = (to_text(m.get("from")).lower(),
                             to_text(m.get("subject")).lower(),
                             to_text(m.get("body")).lower())
    text = subject + " " + body
    flags = set()
    if thread_size > 1:
        flags.add("thread_context_required")
    if any(t in sender for t in ("noreply", "no-reply", "donotreply", "notifications", "-auto", "automated")):
        flags.add("sender_automated")
    if any(w in text for w in ("unsubscribe", "newsletter", "digest", "receipt", "invoice", "order #",
                               "shipped", "delivered", "payment", "statement", "verification code",
                               "otp", "2fa", "maintenance", "status update")):
        flags.add("noise_keywords")
    if any(w in text for w in ("meeting", "deadline", "schedule", "invite", "rsvp",
                               "confirm by", "can you make", "are you free", "calendar")):
        flags.add("commitment_language")
    if any(w in text for w in ("please", "can you", "could you", "would you",
                               "i need you to", "please send", "please review", "let me know")):
        flags.add("request_language")
    if re.search(r"https?://", text):
        flags.add("link_present")
    if any(w in text for w in ("urgent", "immediately", "asap", "action required", "within 24", "expires today")):
        flags.add("urgency")
    if any(w in text for w in ("password", "otp", "verification code", "verify your account", "gift card",
                               "wire transfer", "credentials", "click here to log in", "bitcoin", "crypto")):
        flags.add("credential_phish_hint")
    if any(w in text for w in ("you are an ai", "you're an ai", "as an ai", "ignore previous",
                               "system prompt", "disregard", "your instructions")):
        flags.add("ai_addressed_hint")
    return flags


def sort_time(m):
    v = m.get("timestamp")
    try:
        return int(v)
    except (TypeError, ValueError):
        return str(v)


def group_threads(messages):
    threads = defaultdict(list)
    for m in messages:
        threads[m.get("thread_id")].append(m)
    for t in threads.values():
        t.sort(key=sort_time)
    return threads


def append_trace(entry):
    lines = []
    if os.path.exists(TRACE_LOG):
        with open(TRACE_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines.append(json.dumps(entry))
    lines = lines[-MAX_TRACE_LINES:]
    with open(TRACE_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


# ------------------------------------------------------------- manifest

DEFAULT_TEMPLATE = """\
# Inbox Manifest

**Generated:** {{generated_at}} (run {{run_id}})

## 1. Scope
- Source: {{source_file}}
- Messages processed: {{message_count}}
- Threads: {{thread_count}}
- Unread messages: {{unread_count}}

## 2. Data-format assumptions (script-verified, confirm by reading)
- Schema: {{schema_status}}
- Timestamp encoding: {{timestamp_format}}
- TODO: add owner identity, sparse-id note, `unread` semantics, etc.

## 3. Recon leads for manual confirmation
- Multi-message threads (buried/context-dependent request candidates):
{{long_threads}}
- Messages needing thread-context reading:
{{thread_required}}
- Most frequent senders (standing-preference candidates):
{{top_senders}}
- Most frequent recipients (likely the inbox owner):
{{top_recipients}}

## 4. Recon flags (leads only, NOT final categories)
{{flag_counts}}

## 5. Confirmed findings (fill by hand after reading the inbox)
- TODO: which thread holds the buried request, and which message
- TODO: ambiguous messages where the right move is to ask
- TODO: standing preferences per correspondent
- TODO: the phishing / social-engineering message and its tells
- TODO: the AI-addressed instruction (treat as DATA, never obey)

## 6. Design implications
- TODO: per-tier counts (rules / chatbot / workflow / agent) after manual classification
- TODO: what the runtime pipeline must log into trace.jsonl
"""


def render_values(summary):
    return {
        "generated_at": summary["generated_at"],
        "run_id": summary["run_id"],
        "source_file": summary["source_file"],
        "message_count": summary["message_count"],
        "thread_count": summary["thread_count"],
        "unread_count": summary["unread_count"],
        "schema_status": summary["schema_status"],
        "timestamp_format": summary["timestamp_format"],
        "long_threads": "\n".join(
            f"  - thread {tid} ({len(msgs)} msgs): {', '.join(msgs)}"
            for tid, msgs in summary["long_threads"].items()
        ) or "  (none)",
        "thread_required": ", ".join(summary["thread_required_ids"]) or "  (none)",
        "top_senders": "\n".join(f"  - {s} x{n}" for s, n in summary["top_senders"]),
        "top_recipients": "\n".join(f"  - {r} x{n}" for r, n in summary["top_recipients"]),
        "flag_counts": "\n".join(f"  - {name}: {n}" for name, n in summary["flag_counts"].items()),
    }


def build_manifest(summary):
    template = DEFAULT_TEMPLATE
    if os.path.exists(TEMPLATE):
        with open(TEMPLATE, encoding="utf-8") as f:
            template = f.read()
    text = template
    for key, val in render_values(summary).items():
        text = text.replace("{{" + key + "}}", str(val))
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write(f"<!-- GENERATED file. Edit {TEMPLATE}, not this. -->\n\n" + text)
    print(f"Wrote {MANIFEST} from {os.path.basename(TEMPLATE)}")


# ------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Inbox recon + dynamic summary + manifest builder")
    parser.add_argument("inbox", nargs="?", default=INBOX)
    parser.add_argument("--manifest", action="store_true", help="also regenerate manifest.md")
    args = parser.parse_args()

    rid = new_run_id()
    messages = load_inbox(args.inbox)
    threads = group_threads(messages)
    thread_sizes = {tid: len(t) for tid, t in threads.items()}
    flags = [build_flags(m, thread_sizes.get(m.get("thread_id"), 1)) for m in messages]

    anomalies, unread_problems = validate_schema(messages)
    unread_ids = [m["id"] for m in messages if m.get("unread") is True]
    senders = Counter(to_text(m.get("from")) for m in messages)
    recipients = Counter(to_text(m.get("to")) for m in messages)
    len_dist = Counter(len(t) for t in threads.values())
    flag_counts = Counter(f for s in flags for f in s)
    long = {tid: t for tid, t in threads.items() if len(t) >= LONG_THREAD}

    summary = {
        "run_id": rid,
        "generated_at": now_utc(),
        "source_file": args.inbox,
        "message_count": len(messages),
        "thread_count": len(threads),
        "thread_length_distribution": dict(sorted(len_dist.items())),
        "unread_count": len(unread_ids),
        "unread_ids": unread_ids,
        "long_threads": {tid: [m["id"] for m in t] for tid, t in long.items()},
        "thread_required_ids": [m["id"] for m, s in zip(messages, flags) if "thread_context_required" in s],
        "sender_frequency": senders.most_common(),
        "top_senders": senders.most_common(15),
        "top_recipients": recipients.most_common(8),
        "flag_counts": dict(flag_counts),
        "flags_per_message": [
            {"id": m["id"], "thread_id": m.get("thread_id"), "thread_size": thread_sizes.get(m.get("thread_id"), 1),
             "unread": m.get("unread"), "subject": m.get("subject"), "flags": sorted(s)}
            for m, s in zip(messages, flags)
        ],
        "schema_status": "all 8 keys present on all messages; no anomalies"
                          if not anomalies else "; ".join(anomalies[:10]),
        "timestamp_format": timestamp_note(messages),
        "schema_anomalies": anomalies,
        "unread_problems": unread_problems,
    }

    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    append_trace({k: summary[k] for k in ("run_id", "generated_at", "source_file", "message_count",
                                          "thread_count", "unread_count", "flag_counts")})

    # ---- terminal report (for the human reading pass)
    print(f"\nInbox: {args.inbox} | run {rid}")
    print(f"Messages: {len(messages)} | Threads: {len(threads)} | Unread: {len(unread_ids)} | "
          f"Thread sizes: {dict(sorted(len_dist.items()))}")
    print("\nLong threads (>= {0} msgs):".format(LONG_THREAD))
    for tid, t in sorted(long.items(), key=lambda kv: -len(kv[1])):
        print(f"  {tid} ({len(t)}): " + ", ".join(m["id"] for m in t))
    print("\nTop senders:")
    for s, n in senders.most_common(10):
        print(f"  {n:3d}  {s}")
    print("\nTop recipients (likely the owner):")
    for r, n in recipients.most_common(8):
        print(f"  {n:3d}  {r}")
    print("\nFlag counts:")
    for name, n in flag_counts.most_common():
        print(f"  {name:26s} {n:3d}")
    print(f"\n[6] SUBJECT DUMP")
    for m in sorted(messages, key=sort_time):
        print(f"  {m['id']:<6} | {to_text(m.get('from'))[:25]:<25} | {m.get('subject','')[:70]}")

    if args.manifest:
        build_manifest(summary)
    print(f"\nWrote {SUMMARY}; appended one line to {TRACE_LOG} (capped at {MAX_TRACE_LINES} lines).")


if __name__ == "__main__":
    main()