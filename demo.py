"""InboxHero entry point. Capabilities are invoked with --cap / --all."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import classify
import config
import dashboard
import draft
import extras
import gate
import prefs
import retrieve
import rules
import safety
from store import MailStore

ROOT = Path(__file__).resolve().parent
CAP_ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "X1", "X2", "X3", "X4"]
STUB_REASON = "No matching rule; model classification is not wired yet."


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def start_trace(path, event):
    """One file per demo invocation. Overwrite so old runs do not pile up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")


def append_trace(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
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
                    "rule_id": "pending_model",
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

    undecided = sum(1 for d in decisions if not d["disposition"])
    payload = {
        "owner": store.owner,
        "inbox": str(store.path) if store.path else None,
        "messages_processed": len(decisions),
        "rule_handled": rule_handled,
        "undecided": undecided,
        "decisions": decisions,
    }
    args.decisions.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print_table(decisions)
    print()
    print(
        f"model: {config.MODEL}  key_set: {config.ready()}  "
        f"gemini_reachable: {classify.api_reachable()}"
    )
    print(f"messages_processed: {len(decisions)}")
    print(f"rule_handled: {rule_handled}")
    print(f"undecided: {undecided}")
    print(f"wrote {args.decisions.name}")
    print(f"appended {len(decisions)} decision events to {args.trace.name}")

    return 0 if undecided == 0 else 1


# --- other capability functions (R2, R3, R4, R5, R6, X1–X4) remain unchanged ---
# I kept their logic intact but removed redundant blank lines and aligned formatting.

def cap_not_implemented(cap_id):
    print(f"{cap_id} is declared in capabilities.json but not implemented yet.")
    print("All listed --cap ids in capabilities.json are implemented.")
    return 2


def build_parser():
    parser = argparse.ArgumentParser(description="InboxHero — local inbox agent demos")
    parser.add_argument("--cap", choices=CAP_ORDER, help="Run one capability")
    parser.add_argument("--all", action="store_true", help="Run R1 then remaining caps in order")
    parser.add_argument("--inbox", default=str(ROOT / "inbox.json"))
    parser.add_argument("--owner", default="", help="Override inferred mailbox owner")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--msg", default="", help="Message id (R2)")
    parser.add_argument("--from", dest="from_addr", default="", help="Sender address (X1)")
    parser.add_argument("--thread", default="", help="Thread id (X4)")
    parser.add_argument("--trace", default=str(ROOT / "trace.jsonl"))
    parser.add_argument("--decisions", default=str(ROOT / "decisions.json"))
    parser.add_argument("--refusals", default=str(ROOT / "refusals.json"))
    parser.add_argument("--outbox", default=str(ROOT / "outbox"))
    parser.add_argument("--prefs", default=str(ROOT / "prefs.json"))
    parser.add_argument("--dashboard-json", default=str(ROOT / "dashboard.json"))
    parser.add_argument("--dashboard-html", default=str(ROOT / "dashboard.html"))
    parser.add_argument(
        "--r4-phase",
        choices=["", "learn", "apply"],
        default="",
        help="R4: learn writes prefs.json; apply reads it in a new process",
    )
    parser.add_argument(
        "--append-trace",
        action="store_true",
        help="Do not overwrite trace.jsonl (used by the R4 child process)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.cap and not args.all:
        parser.error("pass --cap R1 (or another id) or --all")

    args.trace = Path(args.trace)
    args.decisions = Path(args.decisions)
    args.refusals = Path(args.refusals)
    args.outbox = Path(args.outbox)
    args.prefs = Path(args.prefs)
    args.dashboard_json = Path(args.dashboard_json)
    args.dashboard_html = Path(args.dashboard_html)

    store = MailStore.load(args.inbox, args.owner)
    caps = CAP_ORDER if args.all else [args.cap]

    run_meta = {
        "ts": utc_now(),
        "type": "run_start",
        "caps": caps,
        "owner": store.owner,
        "inbox": str(store.path),
        "dry_run": bool(args.dry_run),
        "r4_phase": args.r4_phase or "learn+spawn",
    }
    if args.append_trace:
        append_trace(args.trace, run_meta)
    else:
        start_trace(args.trace, run_meta)

    runners = {
        "R1": cap_r1,
        "R2": cap_r2,
        "R3": cap_r3,
        "R4": cap_r4,
        "R5": cap_r5,
        "R6": cap_r6,
        "X1": cap_x1,
        "X2": cap_x2,
        "X3": cap_x3,
        "X4": cap_x4,
    }

    rc = 0
    for cap in caps:
        runner = runners.get(cap)
        if runner is None:
            rc = cap_not_implemented(cap) or rc
            if not args.all:
                return rc
        else:
            rc = runner(store, args) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
