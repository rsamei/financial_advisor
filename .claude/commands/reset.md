# /reset - Previewed, Approved, Reversible

Move local state out of the way without deleting anything. Every reset is previewed, approved
against a token computed from that exact preview, and executed as a **move** into `.reset-trash/`.
`/reset` executes nothing else.

**State separation.** `/reset` writes only `.reset-trash/` and empties `market/queries.json` in
place when that scope is chosen. It never writes anything else: everything it touches, it moves.
Nothing is deleted, and `compliance/` is never in scope.

## Step 0: Parse Input

| `$ARGUMENTS` | Mode |
|---|---|
| `--scope <name>` (repeatable) | profile, market, decisions, advice, ips, track, watch, views, state |
| Empty | Explain the scopes and stop |
| Unknown scope | List the valid scopes and stop |

`compliance` is deliberately not a scope. A refusal log that can be reset is not a refusal log.

## Step 1: Preview

```
python tools/reset_repo.py preview --scope <name>
```

Show the user every file, its size, and the total. The preview prints a **token** derived from that
exact file list, including sizes and modification times.

Say what each scope costs in plain terms:

| Scope | What is lost |
|---|---|
| `profile` | The whole financial picture. `/setup` must run again from documents and questions |
| `market` | The observation corpus and evidence cards. `queries.json` is emptied, so pulls stay replayable but claims lose their cards |
| `decisions` | The tracker and every brief: what was decided and why |
| `advice` | Past action lists. The decisions they created stay in the tracker |
| `ips` | The policy, its lock and its deviations - **moved, never deleted** |
| `track` | The action ledger, snapshots and reviews: the only record of what actually happened, and the only input to knowing whether any of this worked |
| `watch` | Triggers and alerts |
| `views` | Recorded opinions and their scores - including the bad ones, which are the useful ones |
| `state` | Workflow checkpoints and the response cache. Cheap; nothing irreplaceable |

Be specific about `track`: resetting it does not just clear a list, it erases the evidence about
whether this system has been any good.

## Step 2: Approve

Ask for explicit confirmation, naming the scopes and the file count. Then:

```
python tools/reset_repo.py apply --scope <name> --token <token from the preview>
```

If anything changed between preview and apply, the token no longer matches and the tool refuses.
That is correct: re-preview and re-approve rather than forcing it.

## Step 3: Report

Say where the files went (`.reset-trash/<timestamp>/`, with its manifest), what was kept because
git tracks it, and how to restore: move the files back to their original relative paths. Emptying
the trash is the user's own action, and this command never does it.

## Important Rules

- Never apply without a fresh preview and an explicit approval.
- Never delete. Everything moves.
- Never include `compliance/` in any scope.
- Never empty `.reset-trash/`.
- Never reset `profile` or `track` without stating, in plain words, what is being given up.
