# Pinocchio — the honesty layer for Hermes Agent

Every agent user has lived this moment: the agent announces
**"done, all tests pass"** — and the tests do not pass. "Fixed and
deployed," and nothing was deployed. Confident, fluent, wrong.

Pinocchio is a Hermes plugin that keeps the receipts, and holds the
agent's words against them.

## How it works

1. **Receipts.** A `post_tool_call` hook records work evidence for every
   session: commands and their exit codes, test runs, builds, file
   writes, git operations, deploys. Read-only lookups are ignored; a
   receipt exists only where the agent acted and the world answered.
2. **Cross-examination.** When the agent writes its closing message, a
   `transform_llm_output` hook extracts the checkable claims in it
   ("all tests pass", "the build succeeds", "I updated `src/app.py`",
   "changes are committed", "it is deployed") and judges each one
   against the most recent matching receipt:
   - **✓ verified** — the last matching action succeeded
   - **✗ contradicted** — the last matching action failed
   - **? unverified** — no matching action was recorded this session
3. **The stamp.** Verdicts are appended to the message the user actually
   receives, with the evidence cited: *"🤥 pinocchio · "all tests pass"
   → ✗ contradicted (the last test run failed (exit 1) at 19:41:
   pytest tests/)"* — and the nose grows: every contradicted claim is
   counted, all time.
4. **The bounce.** On coding turns, a `pre_verify` hook sends a
   contradicted claim back once, before the turn ends: re-run the
   failing step and confirm it, or correct the claim. Hermes bounds the
   nudges (`agent.max_verify_nudges`), so the agent can never be trapped
   in a loop.

## What it is not

- **Not a second model grading the first.** hermes-verifier attaches an
  advisor LLM to review turns. Pinocchio uses no model at all: the
  comparison is deterministic pattern matching against recorded facts.
- **Not an on-demand checker.** hermes-receipts produces evidence for
  checks you explicitly run. Pinocchio watches every session on its
  own and speaks exactly when a claim is made.
- **Not a prompt.** It needs no cooperation from the agent. The hooks
  observe and annotate regardless of what the model intended to say.

## Tools and commands

- `pinocchio_report` — the session truth table: claims, verdicts,
  evidence, nose count.
- `pinocchio_receipts` — the recorded evidence for a session.
- `/pinocchio` — truth table in chat. `/pinocchio receipts` for the
  evidence list, `/pinocchio nose` for the all-time count.
- Bundled skill `pinocchio-honesty` teaches the agent how to earn
  verified stamps and how to respond to a bounce.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `bounce_on_contradiction` | `true` | Send contradicted closing claims back once on coding turns |
| `show_unverified` | `true` | Include unverified lines in the stamp; turn off to stamp only verified and contradicted claims |

## Privacy and disclosure

Receipts live in a local SQLite database in the active profile's plugin
data directory (Hermes `plugin_db`). Pinocchio makes **no network
calls**, spawns **no shell commands**, starts **no background
processes**, and reads **no credentials**. It stores only tool-call
metadata (tool name, a truncated command/target, status, exit code,
timestamp) and the claim snippets it stamped. Pure Python standard
library, zero dependencies.

## Development

```
python3 tests/test_pinocchio.py   # standalone suite, no Hermes required
hermes plugins validate --install-deps /path/to/pinocchio
hermes plugins doctor --ci
```

## License

MIT © Oluwatoba Daniel
