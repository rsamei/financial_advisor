# /status - What State Is This In

Show what has been populated, what is stale, what is open, and what a workflow was doing when it
stopped. `/status` writes nothing and executes nothing. It is the command to run when returning to
this repository after a gap, or when something failed halfway.

**State separation.** `/status` writes **nothing at all** - it never writes any file in this
repository. It reads `profile/`,
`market/queries.json`, `decision_tracker.csv`, `advice/`, `ips/`, `track/`, `watch/`, `views/` and
`.state/workflows/`.

## Step 0: Parse Input

| `$ARGUMENTS` | Mode |
|---|---|
| Empty | Everything below |
| `--workflow WF-...` | One workflow's checkpoint in detail |
| `--stale` | Only what is out of date |
| Unknown flag | Explain the supported modes and stop |

## Step 1: Gather, Without Writing

```
python tools/profile_check.py check
python tools/ips.py status
python tools/tracker.py list --open
python tools/track_actions.py verify
python tools/compliance_guard.py verify
python tools/views.py list --expired
python tools/watchlist.py list
python tools/workflow_state.py status --workflow <id>
```

Read `10-workflow-checkpoints.md` before interpreting a checkpoint.

## Step 2: Report, Worst First

1. **Interrupted workflows** - any step left `running`, with the command that owns it and what
   `/resume` would do next.
2. **Broken invariants** - a tampered ledger, a modified frozen IPS, a failing evidence card.
   These are not "stale"; they are wrong, and they are reported first.
3. **Stale figures** - balance-sheet rows older than 90 days, observations outside their tier's
   freshness window, and what each one blocks.
4. **Open decisions** - by band: `do_now` not yet executed, `do_scoped` part-done, `park` with and
   without a trigger.
5. **Expired views** that have not been scored, and the running calibration if any exists.
6. **Watchlist**: live triggers, and anything that could not be evaluated.
7. **What is missing entirely** - no profile, no IPS, no market pull in N days.

End with the single next command worth running, and why. If everything is current and nothing is
open, say that in one line rather than filling a page.

## Important Rules

- Never write anything. If a check would modify state, do not run it here.
- Never report a broken invariant as a stale figure.
- Never guess what an interrupted workflow was doing; read the checkpoint.
