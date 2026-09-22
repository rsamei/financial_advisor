# /resume - Continue an Interrupted Workflow

Pick up a workflow that stopped mid-run, without redoing work that already committed and without
trusting work that did not. `/resume` executes nothing that the owning command would not.

**State separation.** `/resume` writes only `.state/workflows/` checkpoints, through
`tools/workflow_state.py`. Every domain write belongs to the command that owns the step - `/market`
writes `market/`, `/advise` writes `advice/`, and so on. `/resume` never writes domain state
itself.

## Step 0: Parse Input

| `$ARGUMENTS` | Mode |
|---|---|
| Empty | List resumable workflows and ask which |
| `WF-YYYYMMDD-xxxxxxxx` | Resume that workflow |
| `--abandon WF-...` | Mark it abandoned, with a reason; write nothing else |
| Unknown flag | Explain the supported modes and stop |

## Step 1: Read the Checkpoint, Then Reconcile

```
python tools/workflow_state.py status    --workflow <id>
python tools/workflow_state.py reconcile --workflow <id>
```

Read `10-workflow-checkpoints.md` first. Reconciliation is what makes resuming safe:

- A step with an **owner receipt** on disk committed its outputs; it is completed from that receipt.
- A step whose **input or output digest changed** is invalidated, along with everything downstream.
  This is the important one: resuming on top of changed inputs is how a half-finished run produces
  a confident, wrong result.
- A step left `running` with no receipt did not commit. It runs again from the start.

## Step 2: Hand Back to the Owning Command

Report which steps are completed, which were invalidated and why, and the next eligible step. Then
run that step **through its owning command**, with the same arguments the checkpoint records.

`/resume` never reimplements a step. If `/market` owns `retrieve-ecb`, `/market` runs it.

## Step 3: Report

Say what was reused, what was redone and why, and what remains. If the workflow cannot be resumed -
inputs gone, definition changed - say so plainly and suggest starting a fresh run rather than
patching a broken one.

## Important Rules

- Never mark a step complete without its receipt.
- Never resume past an invalidated step.
- Never reimplement a step here.
- Never delete another process's lock. A lock left behind means a run was interrupted: inspect what
  it was writing, then let the user remove it.
