# CAPABILITIES.md


**Student:** Gaddam Raja Kumar

**Repository:** https://github.com/grk0519/inboxhero
 

Run everything through one entry point:

 

```

python demo.py --cap R1        # one capability

python demo.py --all           # all of them, in the order below

```

 

Model provider and API keys come from environment variables loaded in `config.py`. Copy `.env.example` to `.env`. Do not commit `.env`.

 

---

 

## The system, in one paragraph

 

A single Python pipeline, no agent framework. Any `inbox.json` with the course schema is loaded, the mailbox owner is inferred, cheap noise (receipts, newsletters, no-reply alerts) is dispatched by rules before any model is touched, and the rest go through classify → retrieve → draft → gate. Email bodies enter the model tagged as untrusted data. Only `gate.py` can send or delete. Preferences and the action log live in JSON files on disk so they survive a process restart. A final pass writes the three-pane dashboard.

 

This file is the human-readable contract. `capabilities.json` is the machine-readable twin; keep them in step. Numbers such as `rule_handled` are filled after R1 actually runs.

 

## Disposition vocabulary

 

| Disposition | Meaning in this system |

|-------------|------------------------|

| `reply` | A draft is warranted (question, request, or commitment that needs an answer). |

| `archive` | No further action; typical of receipts, newsletters, resolved FYI. |

| `defer` | Owner-sent mail, or something to revisit later, not act on now. |

| `delegate` | Someone else should handle it; we record who and why. |

| `escalate` | Human must decide: hostile, phishing, money/legal send, or too ambiguous to guess. |

 

## Design choices you were asked to state

 

- **Framework: none.** The work is a linear pipeline with one branch (rule-path vs model-path). A crew or graph would add moving parts without extra capability. Mapping to framework vocabulary is in the README Final Report (Q4): modules play the roles of agent, task, crew, and router.

 

- **Retrieval: thread-walk**, with keyword search as the fallback for cross-thread facts. The inbox already carries structure in `thread_id`. Embeddings are unnecessary on ~100 messages and would hide citation errors.

 

- **Reversible vs irreversible.** `send` and `delete` are irreversible and gated. `send` writes one file under `outbox/` and cannot be unsent. `delete` is irreversible because the mock store has no trash. `draft`, `label`, `archive`, and `defer` are reversible and do not require a prompt.

 

- **Where the gate sits.** Only two functions can cause an irreversible effect, and both call `require_approval()` first (or no-op under `--dry-run`). A hostile message can influence a *draft*; it cannot write `outbox/` without passing the gate. Tools are declared MCP-style (name + JSON schema) in `gate.py`; we do **not** require a live MCP server for graded `demo.py` commands.

 

- **Escalation line.** Ask a human only for: send to anyone, delete, money/legal, phishing, and embedded-instruction attempts. Internal archives of obvious noise and defers of owner-sent mail are automatic. Trade-off: a noisy internal note could be auto-archived, in exchange for not asking the user to approve forty things.


- **Untrusted mail.** Message bodies are wrapped as data, not as instructions. Tools that send or delete are not reachable from model output except through the gate. Silently ignoring an attack is not enough: we log a refusal, name the id, tell the user, and leave the message in place.


## Capabilities

 

| id | name | tier | one-line claim |

|----|------|------|----------------|

| R1 | Zero the inbox | B | every message gets one disposition + reason, none left |

| R2 | Grounded reply | B | drafts cite the earlier message they used (m008 ← m003) |

| R3 | Gate the irreversible | C | no send/delete without approval or --dry-run |

| R4 | Persistent preference | C | m015 / m041 survive a restart and change later mail |

| R5 | Refuse embedded instructions | C | m024, m017, m039, m047 refused, flagged, reported |

| R6 | Dashboard | C | three panes; [m038, m040] cited; m010 vs m061 conflict |

| X1 | Unread from sender | A | one lookup, unread mail from a given address |

| X2 | Follow-up tracking | B | unanswered owner-sent mail (m044), with a chase draft |

| X3 | Morning digest | B | needs you / can wait / auto-archived |

| X4 | Thread open question | B | t-launch reduced to the pricing-copy ask in m030 |

 

The exact command, observable outcome, and evidence for each is in `capabilities.json`. That file is what a marking script reads.

 

## Notes for filling after implementation

 

- Replace `YOUR NAME, ROLL NUMBER` and the GitHub URL in both files.

- Set `system.model` to the model you actually developed against and the one used in demos.

- Set `system.rule_handled` from a real R1 run, not from `analyze_inbox.py`.

- If an observable id is wrong after the agent runs, fix the observable here — do not hard-code that id in the engine.