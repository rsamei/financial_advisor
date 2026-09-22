---
framework_version: 0.2.0
---

# Workflow Checkpoints

Library file - reached only by an explicit Read from a command; used by `/status`, `/resume` and
any command long enough to be interrupted. `tools/workflow_state.py` implements it.

Ported from research-assistant's `13-workflow-checkpoints.md`; the semantics are unchanged.

## What a checkpoint is for

A `/market` run makes a dozen provider calls, a merge and five agent passes. A `/advise` run gates
six candidate sets. Either can be interrupted - a timeout, a closed terminal, a rate limit. The
question the next session has to answer is not "where did it stop" but **"what can I trust of what
it already did"**, and that is what the checkpoint records.

`tools/workflow_state.py` records **operational metadata only**. It never executes a stored
command, never grants an approval, never writes domain state, and never clears another process's
lock.

## The shape

A workflow definition names:

| Part | Meaning |
|---|---|
| `workflow_type` | `market`, `advise`, `decide`, `setup` or `track` |
| `inputs` | each with a `kind` (`file`, `value`, `domain`), a reference, and a **digest** |
| `steps` | each with `step_id`, `depends_on`, and the `input_names` it consumes |
| `next_action` | what a human would do next |

Every attempt at a step gets an **operation id**: `WF-YYYYMMDD-xxxxxxxx:<step>:<attempt>`. Attempts
are numbered, not overwritten, so a retry is visible as a retry.

## Owner receipts

A step's outputs are committed by the **command that owns it**, and only then does that command
write a receipt naming what it wrote and each output's digest.

Receipts are written **last**, on purpose. A receipt on disk therefore means every output it names
was already committed. The reverse - writing the receipt first - would let a crash leave a
checkpoint claiming work that does not exist, which is worse than a checkpoint claiming nothing.

File outputs are hashed. A shared store that later commands legitimately change - the observation
corpus, the tracker - is recorded as a **domain** output with its own digest, so a later lawful
write does not look like corruption.

## Reconciliation, and why it invalidates

`reconcile` compares the recorded digests with what is on disk:

- **Receipt present, digests match** - the step is completed from its receipt. Nothing is redone.
- **An input digest changed** - that step and **everything downstream of it** are invalidated.
  This is the rule that matters. Resuming on top of changed inputs is how a half-finished run
  produces a confident, wrong answer: the early steps reflect one version of the world and the
  later ones another, and nothing in the output says so.
- **An output digest changed** - same treatment. Something edited what a step produced.
- **Running, no receipt** - the step did not commit. It runs again from the start.

An unrelated input changing invalidates only the steps that consumed it. A rubric version bump
invalidates the scoring step, not the retrieval that preceded it.

## Locks

A lock file carries the owner's PID and start time. An interrupted run **leaves its lock behind on
purpose**: the next run reports it rather than clearing it. Nothing in this repository deletes
another process's lock, because the only safe way to know a process is gone is for a human to look.

## What a resumed run must say

The report after a resume states: which steps were reused and on what receipt, which were
invalidated and why, which ran again, and what remains. A resumed run that reads like a fresh one
is hiding the thing the reader most needs to know.
