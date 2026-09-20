"""inboxHero entry point. Capabilities are invoked with --cap / --all."""
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
    undecided = 0
    for d in decisions:
        if not d["disposition"]:
            undecided += 1
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
    if undecided == 0:
        return 0
    return 1

def cap_r5(store, args):
    findings = safety.scan(store.messages, store.owner)
    injections = []
    phishing = []
    still_present = []
    missing = []
    for f in findings:
        if f["kind"] == "injection":
            injections.append(f)
        if f["kind"] == "phishing":
            phishing.append(f)
        if store.get(f["message_id"]) is None:
            missing.append(f["message_id"])
        else:
            still_present.append(f["message_id"])
    for f in findings:
        append_trace(
            args.trace,
            {
                "ts": utc_now(),
                "cap": "R5",
                "type": "refusal",
                "message_id": f["message_id"],
                "kind": f["kind"],
                "attempted": f["attempted"],
                "action_taken": f["action_taken"],
                "hide_from_user_requested": f["hide_from_user_requested"],
                "targets": list(f["targets"]),
            },
        )
    print("Hostile inbox scan (email treated as untrusted data, not instructions)")
    print()
    if not injections:
        print("FLAGGED injections: none")
    else:
        print("FLAGGED injections:")
        for f in injections:
            print(f"  FLAGGED: {f['message_id']} attempted to {f['attempted']}; not done, left in place.")
    print()
    if phishing:
        print("FLAGGED phishing (reported, not acted on):")
        for f in phishing:
            print(f"  FLAGGED: {f['message_id']} - {f['attempted']}; not done, left in place.")
        print()
    writes = gate.outbox_file_count(args.outbox)
    inj_rows = []
    for f in injections:
        inj_rows.append(
            {
                "message_id": f["message_id"],
                "attempted": f["attempted"],
                "action_taken": f["action_taken"],
                "hide_from_user_requested": f["hide_from_user_requested"],
                "targets": list(f["targets"]),
            }
        )
    phish_rows = []
    for f in phishing:
        phish_rows.append(
            {
                "message_id": f["message_id"],
                "attempted": f["attempted"],
                "action_taken": f["action_taken"],
            }
        )
    payload = {
        "owner": store.owner,
        "injections": inj_rows,
        "phishing": phish_rows,
        "outbox_writes": writes,
        "deleted": missing,
        "left_in_place": still_present,
    }
    args.refusals.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"outbox/ writes this run: {writes}")
    print(f"deleted hostile messages: {len(missing)}")
    print(f"left in place: {len(still_present)}")
    print(f"wrote {args.refusals.name}")
    print("Bodies would enter a model only via safety.wrap_untrusted().")
    if missing:
        return 1
    return 0

def cap_r3(store, args):
    dry_run = bool(args.dry_run)
    interactive = (not dry_run) and sys.stdin.isatty()
    proposals = gate.collect_proposals(store)
    results = gate.run_proposals(
        proposals,
        outbox=args.outbox,
        dry_run=dry_run,
        interactive=interactive,
    )
    print("MCP-shaped tools (local gate, no MCP server required):")
    for t in gate.list_tools():
        print(f"  {t['name']}: {t['description']}")
    print()
    if dry_run:
        mode = "dry-run"
    elif interactive:
        mode = "interactive y/n"
    else:
        mode = "non-interactive deny"
    print(f"Gate mode: {mode}")
    print()
    by_id = {}
    for p in proposals:
        by_id[p["message_id"]] = p
    for res in results:
        prop = by_id.get(res["message_id"])
        if res["outcome"] == "suppressed":
            verb = "WOULD"
        else:
            verb = res["outcome"].upper()
        dest = ""
        if prop:
            dest = prop["args"].get("to") or ""
        print(f"  {verb} {res['tool']} {res['message_id']} -> {dest}  [{res['human']}] {res['detail']}")
        append_trace(
            args.trace,
            {
                "ts": utc_now(),
                "cap": "R3",
                "type": "gate",
                "tool": res["tool"],
                "message_id": res["message_id"],
                "proposed": prop["args"] if prop else {},
                "reason": prop["reason"] if prop else "",
                "source": prop["source"] if prop else "",
                "human": res["human"],
                "outcome": res["outcome"],
                "detail": res["detail"],
                "path": res["path"],
            },
        )
    writes = gate.outbox_file_count(args.outbox)
    print()
    print(f"proposals: {len(proposals)}")
    print(f"outbox/ writes: {writes}")
    if dry_run:
        print("dry-run suppressed all writes (expected 0).")
    if not dry_run and not interactive:
        print("stdin is not a TTY; denied all sends. Re-run with --dry-run, or in a terminal without --dry-run for y/n.")
    return 0

def cap_r2(store, args):
    msg_id = (args.msg or "m008").strip()
    target = store.get(msg_id)
    if target is None:
        print(f"No message {msg_id} in this inbox.")
        return 1
    finding = safety.inspect(target, store.owner)
    if finding:
        print(f"REFUSED draft for {msg_id}: untrusted mail tried to {finding['attempted']}.")
        print("cited: []")
        print("outbox/ writes: 0")
        return 0
    hits = retrieve.retrieve(store, target)
    for hit in hits:
        append_trace(
            args.trace,
            {
                "ts": utc_now(),
                "cap": "R2",
                "type": "read",
                "message_id": hit["message"]["id"],
                "method": hit["method"],
                "snippets": list(hit["snippets"]),
            },
        )
    result = draft.draft_reply(store, target)
    if result is None:
        print(f"No grounded draft for {msg_id}: needed fact is not in the inbox (or not in retrieved mail).")
        print("cited: []")
        print("Draft nothing.")
        append_trace(
            args.trace,
            {
                "ts": utc_now(),
                "cap": "R2",
                "type": "draft",
                "message_id": msg_id,
                "cited": [],
                "status": "empty",
            },
        )
        return 0
    print(f"To: {result['to']}")
    print(f"Subject: {result['subject']}")
    print()
    print(result["body"])
    print(f"cited: {result['cited']}")
    print(f"retrieval: {result['method']}")
    print("outbox/ writes: 0  (R2 drafts only; send is R3/gate.py)")
    append_trace(
        args.trace,
        {
            "ts": utc_now(),
            "cap": "R2",
            "type": "draft",
            "message_id": msg_id,
            "cited": result["cited"],
            "to": result["to"],
            "status": "ok",
        },
    )
    return 0

def cap_r4_apply(store, args):
    data = prefs.load(args.prefs)
    if not data.get("preferences"):
        print(f"No preferences in {args.prefs}. Run without --r4-phase apply first.")
        return 1
    print(f"Loaded {len(data['preferences'])} preference(s) from {args.prefs.name} (fresh process).")
    actions = prefs.apply_inbox(store.messages, data)
    if not actions:
        print("No later messages matched the saved preferences.")
    for act in actions:
        print(f"  {act['message_id']}: {act['detail']}")
        append_trace(
            args.trace,
            {
                "ts": utc_now(),
                "cap": "R4",
                "type": "pref_apply",
                **act,
            },
        )
    return 0

def cap_r4_learn(store, args):
    data = prefs.extract_all(store.messages, store.owner)
    prefs.save(args.prefs, data)
    print(f"Stored {len(data['preferences'])} preference(s) in {args.prefs.name} and exiting this process.")
    for item in data["preferences"]:
        print(f"  {item.get('source_id')}: {item.get('type')} {item}")
        append_trace(
            args.trace,
            {
                "ts": utc_now(),
                "cap": "R4",
                "type": "pref_store",
                **item,
            },
        )
    sys.stdout.flush()
    if args.r4_phase == "learn":
        return 0
    cmd = [
        sys.executable,
        str(ROOT / "demo.py"),
        "--cap",
        "R4",
        "--r4-phase",
        "apply",
        "--append-trace",
        "--inbox",
        str(store.path or (ROOT / "inbox.json")),
        "--prefs",
        str(args.prefs),
        "--trace",
        str(args.trace),
    ]
    print()
    print("Starting a new process to apply prefs.json ...")
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode

def cap_r4(store, args):
    if args.r4_phase == "apply":
        return cap_r4_apply(store, args)
    return cap_r4_learn(store, args)

def cap_r6(store, args):
    data = dashboard.build(store, prefs_path=args.prefs)
    dashboard.write(data, args.dashboard_json, args.dashboard_html)
    print(f"pending: {len(data['pending'])}")
    print(f"flagged: {len(data['flagged'])}")
    print(f"commitments: {len(data['commitments'])}")
    for conf in data["conflicts"]:
        print(conf["label"], "ids=", conf["ids"])
    multi = [c for c in data["commitments"] if len(c.get("ids") or []) > 1]
    if multi:
        print("multi-message commitments:")
        for c in multi:
            print(f"  {c['ids']}  {c.get('when')}  {c.get('title')}")
    print(f"wrote {args.dashboard_json.name}")
    print(f"wrote {args.dashboard_html.name}")
    append_trace(
        args.trace,
        {
            "ts": utc_now(),
            "cap": "R6",
            "type": "dashboard",
            "pending": len(data["pending"]),
            "flagged": len(data["flagged"]),
            "commitments": len(data["commitments"]),
            "conflicts": data["conflicts"],
            "html": str(args.dashboard_html),
            "json": str(args.dashboard_json),
        },
    )
    return 0

def cap_x1(store, args):
    sender = (args.from_addr or "").strip()
    if not sender:
        print("X1 needs --from someone@example.com")
        return 1
    rows = extras.unread_from(store, sender)
    print(json.dumps({"from": sender, "unread": rows}, indent=2))
    append_trace(args.trace, {"ts": utc_now(), "cap": "X1", "type": "unread_from", "from": sender, "ids": [r["id"] for r in rows]})
    return 0

def cap_x2(store, args):
    rows = extras.followups(store)
    print(json.dumps(rows, indent=2))
    append_trace(args.trace, {"ts": utc_now(), "cap": "X2", "type": "followup", "ids": [r["message_id"] for r in rows]})
    return 0

def cap_x3(store, args):
    data = extras.digest(store)
    print("Needs you")
    for row in data["needs_you"]:
        print(f"  {row['id']}: {row['why']}")
    print("Can wait")
    for row in data["can_wait"]:
        print(f"  {row['id']}: {row['why']}")
    print("Auto-archived")
    print(f"  {data['auto_archived']['count']} messages ({data['auto_archived']['note']})")
    append_trace(
        args.trace,
        {
            "ts": utc_now(),
            "cap": "X3",
            "type": "digest",
            "needs_you": [r["id"] for r in data["needs_you"]],
            "can_wait": [r["id"] for r in data["can_wait"]],
            "auto_archived": data["auto_archived"]["count"],
        },
    )
    return 0

def cap_x4(store, args):
    tid = (args.thread or "t-launch").strip()
    data = extras.thread_open_question(store, tid)
    if not data:
        print(f"No thread {tid}")
        return 1
    print(json.dumps(data, indent=2))
    append_trace(args.trace, {"ts": utc_now(), "cap": "X4", "type": "thread_open", **data})
    return 0

def cap_not_implemented(cap_id):
    print(f"{cap_id} is declared in capabilities.json but not implemented yet.")
    print("All listed --cap ids in capabilities.json are implemented.")
    return 2

def build_parser():
    parser = argparse.ArgumentParser(description="inboxHero — local inbox agent demos")
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
    if args.all:
        caps = CAP_ORDER
    else:
        caps = [args.cap]
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
