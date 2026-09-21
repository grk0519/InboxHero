# InboxHero

InboxHero is a local Python inbox-triage demo. It loads mailbox data from `inbox.json`, applies deterministic safety and routing rules, optionally uses Gemini for messages that rules cannot classify, drafts grounded replies, gates irreversible actions, and writes traceable JSON and HTML output.

The project uses Python's standard library and does not require an agent framework.

## Features

- Classifies every message into a disposition such as `reply`, `archive`, `defer`, `delegate`, or `escalate`.
- Routes obvious noise with rules before model classification.
- Drafts replies using facts retrieved from the same thread or related messages.
- Treats email bodies as untrusted data.
- Requires approval or `--dry-run` before send and delete actions.
- Stores preferences across separate process runs.
- Produces a dashboard of pending actions, flagged messages, commitments, and conflicts.
- Provides focused utilities for unread mail, follow-ups, digests, and open thread questions.

## Requirements

- Python 3.10 or newer is recommended.
- Windows PowerShell, Command Prompt, macOS Terminal, or Linux shell.
- A Gemini API key is optional. The rule-based and safety features can run without one.

## Create a virtual environment

From the project directory, create a virtual environment:

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation for the current user, run PowerShell as the current user and use:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

### Windows Command Prompt

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Confirm that the virtual environment is active:

```text
python --version
```

The prompt normally shows `(.venv)` when it is active. This project currently uses only Python standard-library modules, so no package installation is required.

To leave the environment later, run:

```text
deactivate
```

## Configuration

`config.py` reads settings from a local `.env` file. Keep this file private and do not commit API keys.

Example `.env` values:

```dotenv
MODEL_PROVIDER=gemini
GEMINI_MODEL=gemini-3.5-flash
GEMINI_API_KEY=your_key_here
GEMINI_SLEEP_SECONDS=4
GEMINI_MAX_CALLS=0
GEMINI_RETRIES=0
GEMINI_RETRY_WAIT=5
GEMINI_MAX_FAILURES=2
GEMINI_MAX_SECONDS=45
```

`GEMINI_API_KEY` is optional for local rule and safety demonstrations. When no usable key is configured, unmatched messages use the fallback classification path and are escalated with a stub reason.

## Run the CLI

All capabilities use `demo.py` as the entry point. Run commands from the project directory with the virtual environment active.

Run one capability:

```text
python demo.py --cap R1
```

Run all capabilities in order:

```text
python demo.py --all
```

Display the available command-line options:

```text
python demo.py --help
```

The default mailbox is `inbox.json`. Optional path arguments let you use another input file or output location, for example:

```text
python demo.py --cap R1 --inbox path\to\inbox.json
```

## How `demo.py` works

`demo.py` coordinates the application but delegates mailbox logic to the project modules.

1. `build_parser()` defines the CLI options, including `--cap`, `--all`, `--msg`, `--from`, `--thread`, `--dry-run`, and output paths.
2. `main()` parses the arguments and converts output paths to `Path` objects.
3. `MailStore.load()` reads the inbox JSON without modifying it, validates the message schema, infers the owner, and builds message and thread indexes.
4. `main()` starts or appends to `trace.jsonl` with a `run_start` event.
5. The selected capability runner is looked up in the `runners` dictionary.
6. The runner processes the mailbox and writes capability-specific output.
7. Important decisions and actions are appended to `trace.jsonl`.
8. The runner returns a status code, which `demo.py` passes to the operating system.

With `--all`, the runners execute in this order:

```text
R1, R2, R3, R4, R5, R6, X1, X2, X3, X4
```

R4 is special: its learn phase saves preferences, then starts a fresh `demo.py` process to apply them. This demonstrates that preferences persist on disk rather than only in memory.

## Capabilities

| ID | Name | Example | Purpose |
|---|---|---|---|
| R1 | Zero the inbox | `python demo.py --cap R1` | Assigns every message a disposition and reason. Writes `decisions.json`. |
| R2 | Grounded reply | `python demo.py --cap R2 --msg m008` | Retrieves earlier facts and drafts a reply with cited message IDs. |
| R3 | Gate the irreversible | `python demo.py --cap R3 --dry-run` | Shows send/delete proposals without writing to `outbox/`. |
| R4 | Persistent preference | `python demo.py --cap R4` | Learns preferences, saves `prefs.json`, and applies them in a new process. |
| R5 | Refuse embedded instructions | `python demo.py --cap R5` | Flags hostile or phishing content and leaves messages in place. |
| R6 | Dashboard | `python demo.py --cap R6` | Writes `dashboard.json` and `dashboard.html`. |
| X1 | Unread from sender | `python demo.py --cap X1 --from raghav@paperjet.io` | Lists unread messages from one sender. |
| X2 | Follow-up tracking | `python demo.py --cap X2` | Finds unanswered owner-sent messages and creates chase drafts. |
| X3 | Morning digest | `python demo.py --cap X3` | Separates messages into needs-you, can-wait, and auto-archived groups. |
| X4 | Thread open question | `python demo.py --cap X4 --thread t-launch` | Summarizes the remaining open question in a thread. |

The machine-readable capability contract is in `capabilities.json`. The human-readable design notes are in `CAPABILITIES.md`.

## Processing flow

```text
inbox.json
    |
    v
MailStore.load()
    |
    v
rules.decide() and safety.inspect()
    |                     |
    | rule match          | no confident rule
    v                     v
routing decision       classify.classify_one()
                              |
                              v
                    retrieve -> draft
                              |
                              v
                       gate approval
                              |
                              v
                         outbox/
```

The actual capability selected by `demo.py` determines which parts of this flow run. For example, R2 performs retrieval and drafting, while R3 focuses on the approval gate.

## Safety model

- `rules.py` handles predictable routing such as owner-sent messages, preferences, noise, and ambiguous requests.
- `safety.py` inspects messages for prompt injection and phishing signals.
- Email bodies are treated as data, not as instructions to the program.
- `gate.py` is the only path that can write irreversible send or delete results.
- `--dry-run` reports what would happen without writing action files.
- R5 records refusals in `refusals.json`, logs them, reports them, and leaves hostile messages in the inbox.

## Generated files

| File or directory | Description |
|---|---|
| `decisions.json` | R1 decisions for each processed message. |
| `refusals.json` | R5 injection and phishing findings. |
| `prefs.json` | Preferences learned by R4. |
| `dashboard.json` | Structured R6 dashboard data. |
| `dashboard.html` | Human-readable R6 dashboard. |
| `trace.jsonl` | One JSON event per run, decision, read, draft, gate action, or capability result. |
| `outbox/` | Files written only for approved irreversible actions. |

These files are runtime artifacts. Review `.gitignore` before committing generated output.

## Validation

Compile the Python modules:

```text
python -m compileall .
```

Run the CLI parser without executing a capability:

```text
python demo.py --help
```

A useful safe smoke test is:

```text
python demo.py --cap R3 --dry-run
```

This should show proposed actions while reporting zero new outbox writes for the dry run.

## Project layout

```text
InboxHero/
|-- inbox.json          # Source mailbox data
|-- demo.py             # CLI entry point and capability dispatcher
|-- store.py            # Read-only mailbox loading and indexes
|-- rules.py            # Deterministic routing rules
|-- safety.py           # Injection and phishing checks
|-- classify.py         # Optional Gemini classification
|-- retrieve.py         # Earlier-message and keyword retrieval
|-- draft.py            # Grounded reply drafting
|-- gate.py             # Approval and outbox enforcement
|-- prefs.py             # Preference extraction and application
|-- extras.py            # X1-X4 helper capabilities
|-- dashboard.py         # Dashboard generation
|-- config.py            # Environment and model configuration
|-- capabilities.json    # Machine-readable capability contract
|-- CAPABILITIES.md      # Human-readable capability notes
`-- .env                # Local configuration; never commit secrets
```

## Notes

- `inbox.json` is loaded read-only by `MailStore`.
- `--cap` accepts `R1`, `R2`, `R3`, `R4`, `R5`, `R6`, `X1`, `X2`, `X3`, and `X4`.
- Use `--dry-run` with R3 when inspecting proposed irreversible actions.
- Use a separate output directory or restore generated JSON files when repeating demos and comparing traces.

## FINAL REPORT

### 1) Refused to automate

- I refused to automate irreversible actions like sending or deleting mail.
- Example: `m024` tries to forward the mailbox and hide the instruction, and the system refuses it.
- This is blocked because `send` and `delete` are dangerous, and only [gate.py](gate.py) can do them.
- I drew the line there to stop hostile email instructions from becoming real actions.

### 2) Untrusted text boundary

- Untrusted text enters through the inbox loaded by [store.py](store.py).
- The system treats email content as data, not instructions.
- It only follows actions after checks in [safety.py](safety.py), [rules.py](rules.py), and the gate in [gate.py](gate.py).
- An attacker would need to defeat all three layers before the system acts on their behalf.

### 3) Accountability for wrong sends

- The human owner or operator is accountable if a wrong message is sent.
- The system records the exact message, route, reason, and approval in `trace.jsonl` and the JSON outputs.
- This helps trace the failure back to the source message and the gate decision.
- So the project makes the audit trail visible instead of hiding responsibility.

### 4) My own machinery

- [demo.py](demo.py) is the router because it chooses the capability and dispatches the task.
- The tasks are the capability functions like `cap_r1`, `cap_r2`, and `cap_r3`.
- The agents are modules like [rules.py](rules.py), [safety.py](safety.py), [retrieve.py](retrieve.py), [draft.py](draft.py), and [gate.py](gate.py).
- The full module set acts like the crew, and a framework would add structure, but this custom pipeline is clearer and safer for a small assignment project.
