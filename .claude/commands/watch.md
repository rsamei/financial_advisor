# /watch - Triggers, Never Actions

Write down the conditions the user wants to be told about, in the words they choose while calm, and
check them against recorded observations. A fired trigger produces an alert and a draft decision.
It never produces an action, and `/watch` executes nothing.

**State separation.** `/watch` writes only `watch/watchlist.json` and `watch/alerts/`. It reads
`market/observations.json`, `decision_tracker.csv` and `profile/`. It never writes `profile/**`,
`market/**`, `decisions/**`, `advice/**`, `ips/**` or `track/**`. The draft tracker row an alert
implies is created by `/decide`, not here.

## Step 0: Parse Input

| `$ARGUMENTS` | Mode |
|---|---|
| `add "<subject> <condition>" --then "<what to do>"` | Record a trigger |
| `remove W-0NN` | Remove a trigger |
| `list [--all]` | Live triggers; `--all` includes fired ones |
| `evaluate` | Check every live trigger against recorded observations (also run by `/market`) |
| Unknown flag | Explain the supported modes and stop |

## Step 1: `add` - Ask for the "Then" First

```
python tools/watchlist.py add --subject SYN --metric price --op below --value 90 \
  --then "<the user's own words>" --expires 2027-06-30
```

Two questions, in this order, before recording anything:

1. **"What would you want to do if this happened?"** Record the answer verbatim. This is the whole
   value of a watchlist: a decision made now, in the user's own words, that their frightened or
   excited self will be shown later.
2. **"When should this stop mattering?"** A trigger with no expiry is a condition nobody will ever
   revisit. Ask for a date; record `null` only if the user genuinely wants it open-ended, and say
   that it will keep firing.

A `park` band from `/advise` or `/decide` **must** create a trigger here. That is what makes a park
different from forgetting.

## Step 2: `evaluate` - Three Outcomes, Not Two

```
python tools/watchlist.py evaluate --write-alerts
```

- **Fired** - the condition occurred. Write the alert.
- **Not fired** - it was checked and did not occur.
- **Not evaluated** - no recorded observation, or the newest is more than a week old. Report this
  separately and prominently. A trigger whose data never arrived has not been checked, and calling
  that "not fired" is a watchlist that silently stopped watching.

Never evaluate against a remembered price. If the observation is missing, say which `/market` pull
would supply it.

## Step 3: An Alert Says One Thing

The alert template in `09-reporting-templates.md` quotes the condition, the observation with its
`EV-` key, and **the user's own "then" text**. Then it stops.

Do not attach fresh analysis, a recommendation, or a revised thesis to an alert. The user set this
trigger precisely so that the decision would be made now and not in the moment the condition fires.
Create the draft row via `/decide --re-vet` and let the gates run.

## Step 4: Report

Say what fired, what did not, and - separately - what could not be evaluated and why. List triggers
expiring within 30 days and ask whether each still matters. If nothing fired, say so in one line:
an uneventful watchlist is the normal case, and padding it out invites the user to act.

## Important Rules

- Never write a trigger without the user's own "then" text.
- Never let an alert carry a recommendation.
- Never report "not evaluated" as "not fired".
- Never fire on a remembered or estimated price.
- Never create the tracker row here; `/decide` owns vetting.
- A trigger that has fired stays in the file, marked, so `--all` can show what was asked for and
  what happened.
