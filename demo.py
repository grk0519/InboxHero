"""Entry Point. capabilities are invoked with --cap / all"""

from pathlib import Path
from datetime import datetime, timezone
import json
import argparse
import subprocess
import sys

import rules
import classify
import config


ROOT = Path(__file__).resolve().parent
CAP_ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "X1", "X2", "X3", "X4"]
STUB_REASON = "No Matching rule; model classification is not wired yet."

def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def start_trace(path, event):
    path.parent.mkdir(parent=True, exit_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")

def append_trace(path, event):
    path.parent.mkdir(parents=True, exit_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")

def print_table(rows):
    print(f"{'id':<8} {'disp':<10} {'via':<8} reason")
    print("-" * 88)
    for row in rows:
        reason = (row["reason"] or "").replace("\n", " ")
        if len(reason) > 64:
            reason = reason[:61] + "..."
        print(f"{row['id']:<8} {row['disposition']:<10} {row['via']:<8} {reason}")

def cap_r1(store, args):
    decisions = []
    rule_handled = 0
    for msg in store.messages:
        hit = rules.decide(msg, store.owner, store.thread_for(msg))
        if hit:
            rule_handled += 1
            row = {
                "id": msg["id"],
                "disposition": hit["disposition"],
                "reason": hit["reason"],
                "via": hit["via"],
                "rule_id": hit["rule_id"],
            }
        else:
            llm = classify.classify_one(msg)
            if llm:
                row = {
                    "id": msg["id"],
                    "disposition": llm["disposition"],
                    "reason": llm["reason"],
                    "via": llm["via"],
                    "rule_id": llm["rule_id"],

                }
            else:
                row = {
                    "id": msg["id"],
                    "disposition": "escalate",
                    "reason": STUB_REASON,
                    "via": "stub",
                    "rule_id": "pending_module",
                }
        decisions.append(row)
        append_trace(
            args.trace,
            {
                 "ts": utc_now(),
                 "cap": "R1",
                 "type": "decision",
                 "message_id": row["id"],
                 "disposition": row["disposition"],
                 "reason": row["reason"],
                 "via": row["via"],
                 "rule_id": row["rule_id"],
            },
        )
    undecided = 0
    for d in decisions:
        if not d["disposition"]:
            undecided += 1
    payload = {
        "owner": store.owner,
        "inbox": str(store.path) if store.path else None,
        "message_processed": len(decisions),
        "rules_handled": rule_handled,
        "undecided": undecided,
        "decisions": decisions,
    }
    args.decisions.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print_table(decisions)
    print()
    print(
        f"model: {config.MODEL} key_set: {config.ready()}"
        f"gemini_reachable: {classify.api_reachable()}"
    )
    print(f"messages_processed: {len(decisions)}")
    print(f"rule_handled: {rule_handled}")
    print(f"undecided: {undecided}")
    print(f"wrote {args.decisions.name}")
    print(f"appended {len(decisions)} decesion events to {args.trace.name}")
    if undecided == 0:
        return 0
    return 1
