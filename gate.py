"""Irreversible tools. Named send/delete with a schema, one execution path.

Graded demos run with `python demo.py --cap R3 --dry-run`. No live MCP server.
"""

import json
import re
from pathlib import Path

import rules
import safety

 

TOOL_CATALOG = [

    {

        "name": "send",

        "description": "Write one outbound message under outbox/. Irreversible.",

        "inputSchema": {

            "type": "object",

            "required": ["message_id", "to", "subject", "body"],

            "properties": {

                "message_id": {"type": "string"},

                "to": {"type": "string"},

                "subject": {"type": "string"},

                "body": {"type": "string"},

            },

        },

        "irreversible": True,

    },

    {

        "name": "delete",

        "description": "Remove a message from the store. Irreversible (no trash in the mock).",

        "inputSchema": {

            "type": "object",

            "required": ["message_id"],

            "properties": {"message_id": {"type": "string"}},

        },

        "irreversible": True,

    },

]

 

def list_tools():

    return list(TOOL_CATALOG)

 

def _safe_filename(message_id):

    return re.sub(r"[^A-Za-z0-9._-]+", "_", message_id) + ".json"

 

def write_outbox(outbox, args):

    outbox = Path(outbox)

    outbox.mkdir(parents=True, exist_ok=True)

    path = outbox / _safe_filename(str(args.get("message_id") or "unknown"))

    payload = {

        "to": args.get("to"),

        "subject": args.get("subject"),

        "body": args.get("body"),

        "in_reply_to": args.get("message_id"),

    }

    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return path

 

def outbox_file_count(outbox):

    outbox = Path(outbox)

    if not outbox.exists() or not outbox.is_dir():

        return 0

    n = 0

    for p in outbox.iterdir():

        if p.is_file():

            n += 1

    return n

 

def collect_proposals(store):
    """What send/delete the system would attempt."""
    proposals = []

    for msg in store.messages:
        mid = msg["id"]
        finding = safety.inspect(msg, store.owner)

        if finding:
            tool = "delete" if "delete" in finding["attempted"].lower() else "send"

            if finding["targets"]:
                to_addr = finding["targets"][0]
            else:
                to_addr = msg.get("from")

            proposals.append(
                {
                    "tool": tool,
                    "message_id": mid,
                    "args": {
                        "message_id": mid,
                        "to": to_addr,
                        "subject": msg.get("subject") or "",
                        "body": (
                            "Blocked. Untrusted mail asked the assistant to "
                            + finding["attempted"]
                            + "."
                        ),
                    },
                    "reason": "Untrusted mail tried to " + finding["attempted"],
                    "source": "untrusted_email",
                    "auto_deny": True,
                }
            )
            continue

        hit = rules.decide(msg, store.owner, store.thread_for(msg))
        skip_ids = {"noise", "owner_sent", "owner_preference", "ambiguous"}

        if hit and hit["rule_id"] in skip_ids:
            continue
        if hit and hit["disposition"] in {"archive", "defer"}:
            continue

        to_addr = msg.get("from") or ""
        subj = msg.get("subject") or ""
        if subj.lower().startswith("re:"):
            re_subj = subj
        else:
            re_subj = "Re: " + subj

        proposals.append(
            {
                "tool": "send",
                "message_id": mid,
                "args": {
                    "message_id": mid,
                    "to": to_addr,
                    "subject": re_subj,
                    "body": (
                        f"[inboxHero placeholder draft for {mid}] "
                        "A real grounded body is produced in R2. "
                        "This would be sent only if the gate allows."
                    ),
                },
                "reason": "Candidate reply; send is irreversible so it stays behind the gate.",
                "source": "pipeline",

                "auto_deny": False,

            }

        )

    return proposals

 

def prompt_yes_no(question):

    try:

        raw = input(f"{question} [y/N] ").strip().lower()

    except EOFError:

        return False

    return raw in {"y", "yes"}

 

def call_tool(tool, args, outbox, dry_run, auto_deny=False, deny_reason="", approve=None):

    """Only place that may write outbox/."""

    mid = str(args.get("message_id") or "")

    names = [t["name"] for t in TOOL_CATALOG]

    if tool not in names:

        return {

            "tool": tool,

            "message_id": mid,

            "human": "n/a",

            "outcome": "rejected",

            "detail": "unknown tool: " + tool,

            "path": None,

        }

 

    if auto_deny:

        return {

            "tool": tool,

            "message_id": mid,

            "human": "policy_deny",

            "outcome": "skipped",

            "detail": deny_reason or "blocked by safety policy",

            "path": None,

        }

 

    if dry_run:

        return {

            "tool": tool,

            "message_id": mid,

            "human": "dry-run",

            "outcome": "suppressed",

            "detail": "shown only; outbox not written",

            "path": None,

        }

 

    if tool == "delete":

        allowed = approve(f"DELETE {mid}?") if approve else False

        if not allowed:

            return {

                "tool": tool,

                "message_id": mid,

                "human": "n",

                "outcome": "skipped",

                "detail": "delete not approved (message left in place)",

                "path": None,

            }

        return {

            "tool": tool,

            "message_id": mid,

            "human": "y",

            "outcome": "skipped",

            "detail": "delete refused by design: no trash, leave in place",

            "path": None,

        }

 

    allowed = approve(f"SEND {mid} to {args.get('to')}?") if approve else False

    if not allowed:

        return {

            "tool": tool,

            "message_id": mid,

            "human": "n",

            "outcome": "skipped",

            "detail": "send not approved",

            "path": None,

        }

    path = write_outbox(outbox, args)

    return {

        "tool": tool,

        "message_id": mid,

        "human": "y",

        "outcome": "written",

        "detail": "wrote outbox file",

        "path": str(path),

    }

 

def run_proposals(proposals, outbox, dry_run, interactive):

    approve = prompt_yes_no if interactive and not dry_run else None

    results = []

    for p in proposals:

        results.append(

            call_tool(

                p["tool"],

                p["args"],

                outbox,

                dry_run,

                auto_deny=p["auto_deny"],

                deny_reason=p["reason"],

                approve=approve,

            )

        )

    return results