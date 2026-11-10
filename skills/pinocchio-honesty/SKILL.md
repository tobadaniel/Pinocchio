---
name: pinocchio-honesty
description: Work honestly under Pinocchio, the receipt checker. Your closing claims are cross-examined against the tool calls you actually made; learn how to earn verified stamps and what to do when a claim is bounced back.
---

# Working under Pinocchio

Pinocchio records receipts for your tool calls (commands and exit codes,
test runs, builds, file writes, git operations, deploys) and checks the
claims in your closing message against them. Each checkable claim is
stamped **verified**, **unverified**, or **contradicted**, and on coding
turns a contradicted claim is sent back to you once before the turn ends.

## Earn verified stamps

- **Run it before you claim it.** "All tests pass" is verified only when
  a test run in this session actually exited 0. Run the tests first,
  then claim.
- **Claim only what the session shows.** If you edited a file but never
  ran the build, say the edit is done and the build is unrun, not that
  everything works.
- **Be specific.** "The parser tests pass and the file
  `src/parser.py` is updated" checks better than "all done", because
  specific claims map to specific receipts.
- **No receipt, no claim.** Commits, pushes, and deploys count only when
  the command ran here. Work done elsewhere or planned for later should
  be described as such.

## When a claim is bounced back

A bounce means the receipts contradict your closing text: for example
you wrote that the tests pass, but the last test run failed. Do not
argue with it. Either:

1. Re-run the failing step, fix what it shows, and confirm it passes, or
2. Correct the claim: state plainly what failed and what remains undone.

Then finish. A corrected claim is a good outcome; the nose only grows
for claims that reach the user contradicted.

## Checking yourself

Call `pinocchio_receipts` to see what evidence the session holds before
you write your closing message, and `pinocchio_report` for the truth
table of claims already stamped. If a receipt you expected is missing,
the action did not happen in this session, whatever you intended.
