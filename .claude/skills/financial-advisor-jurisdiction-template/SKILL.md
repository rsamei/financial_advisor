---
name: financial-advisor-jurisdiction-template
description: "Skeleton for adding a second tax and regulatory jurisdiction as an overlay to the base financial-advisor rubric. Copy the directory, rename it, populate every rule row from a dated official source, and point the profile's tax_residency at it. Triggers on: add a jurisdiction, second tax residency, overlay for, moving to, non-Italian resident."
allowed-tools: Read, Glob, Grep
framework_version: 0.1.1
---

# Jurisdiction overlay template

Copy this directory to `financial-advisor-<jurisdiction>/`, then populate
`07-jurisdiction-<code>.md` with the same table shape as
`../financial-advisor/07-jurisdiction-italy.md`:

`Rule | Value | Applies to | Official source (URL) | Verified on | Notes`

Rules that hold for every overlay, and are tested by `tests/test_overlay_contract.py`:

1. **An overlay adds specifics; it never loosens a base threshold.** If the base rubric caps
   crypto at 5 % for a moderate band, an overlay may make it stricter and may never widen it.
2. **No row is usable without an official source URL and a `verified_on` date.** An unverified row
   is `_(unset)_`, and Gate 3 FLAGs any decision that depends on it.
3. An overlay never introduces a new gate, a new weight or a new band. Those live in
   `04-decision-evaluation.md` alone.
4. The compliance reviewer cites overlay rows by id, so every row needs one.

## Build status

Not yet populated. The overlay mechanism is not implemented; it will be generalised from the
Italian overlay in `.claude/skills/financial-advisor/07-jurisdiction-italy.md`.
