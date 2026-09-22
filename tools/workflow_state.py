#!/usr/bin/env python3
"""Maintain explicit, dependency-aware checkpoints for resumable financial-advisor workflows.

This tool records operational metadata only. It never executes a stored command, grants approval,
clears another process's lock, or writes domain state such as the market corpus, the tracker or an action list.

Examples:
    python tools/workflow_state.py init --definition workflow.json
    python tools/workflow_state.py start --workflow WF-20260917-ab12cd34 --step retrieve-ecb
    python tools/workflow_state.py complete --workflow ... --step ... --details completion.json
    python tools/workflow_state.py status --workflow ...
    python tools/workflow_state.py reconcile --workflow ...

Stdlib only. Exit 0 on success and 1 with a concise reason on stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, atomic_write_text, exclusive_lock  # noqa: E402

SCHEMA_VERSION = 1
WORKFLOW_TYPES = ("market", "advise", "decide", "setup", "track")
STEP_STATES = ("pending", "running", "completed", "failed", "blocked", "invalidated")
INPUT_KINDS = ("file", "value", "domain")
OUTPUT_KINDS = ("file", "domain")
ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
WORKFLOW_ID = re.compile(r"WF-\d{8}-[0-9a-f]{8}(?:-\d+)?")
SHA256 = re.compile(r"[0-9a-f]{64}")


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def value_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StateError(f"{name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise StateError(f"{name} must be a list")
    return value


def _text(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{name} must be a non-empty string" + (" or null" if nullable else ""))
    return value.strip()


def _timestamp(value: Any, name: str) -> str:
    value = _text(value, name)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise StateError(f"{name} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise StateError(f"{name} must include a timezone")
    return value


def safe_relative(root: Path, value: str, name: str) -> Path:
    relative = Path(_text(value, name))
    if relative.is_absolute() or ".." in relative.parts:
        raise StateError(f"{name} must be a path inside the repository")
    root = root.resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise StateError(f"{name} escapes the repository")
    return resolved


def load_json(root: Path, path: Path, name: str) -> Any:
    resolved = safe_relative(root, str(path), name) if not path.is_absolute() else path.resolve()
    if root.resolve() not in resolved.parents:
        raise StateError(f"{name} must be inside the repository")
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError(f"{name} is unreadable or invalid JSON: {exc}") from None


def validate_artifact(root: Path, raw: Any, name: str, kinds: tuple[str, ...]) -> dict[str, str]:
    artifact = _object(raw, name)
    if set(artifact) != {"name", "kind", "reference", "digest"}:
        raise StateError(f"{name} must contain exactly name, kind, reference, digest")
    artifact_name = _text(artifact["name"], f"{name}.name")
    if not ID.fullmatch(artifact_name):
        raise StateError(f"{name}.name must match {ID.pattern}")
    kind = _text(artifact["kind"], f"{name}.kind")
    if kind not in kinds:
        raise StateError(f"{name}.kind must be one of {', '.join(kinds)}")
    reference = _text(artifact["reference"], f"{name}.reference")
    supplied_digest = _text(artifact["digest"], f"{name}.digest")
    if not SHA256.fullmatch(supplied_digest):
        raise StateError(f"{name}.digest must be a lowercase SHA-256 digest")
    if kind == "file":
        path = safe_relative(root, reference, f"{name}.reference")
        if not path.is_file():
            raise StateError(f"{name} file does not exist: {reference}")
        if file_digest(path) != supplied_digest:
            raise StateError(f"{name} file digest does not match: {reference}")
    elif kind == "value" and value_digest(reference) != supplied_digest:
        raise StateError(f"{name} value digest does not match its reference")
    return {"name": artifact_name, "kind": kind, "reference": reference, "digest": supplied_digest}


def validate_definition(root: Path, raw: Any) -> dict[str, Any]:
    definition = _object(raw, "definition")
    expected = {"schema_version", "workflow_type", "workflow_version", "target", "linked_ids",
                "inputs", "steps", "next_action", "unresolved_questions"}
    if set(definition) != expected:
        raise StateError("definition has an unexpected shape")
    if definition["schema_version"] != SCHEMA_VERSION:
        raise StateError(f"schema_version must be {SCHEMA_VERSION}")
    workflow_type = _text(definition["workflow_type"], "workflow_type")
    if workflow_type not in WORKFLOW_TYPES:
        raise StateError("workflow_type must be one of " + ", ".join(WORKFLOW_TYPES))
    linked_ids = _object(definition["linked_ids"], "linked_ids")
    if any(not isinstance(key, str) or not isinstance(value, str) or not value.strip()
           for key, value in linked_ids.items()):
        raise StateError("linked_ids must map strings to non-empty strings")
    questions = _list(definition["unresolved_questions"], "unresolved_questions")
    if any(not isinstance(value, str) or not value.strip() for value in questions):
        raise StateError("unresolved_questions must contain non-empty strings")

    inputs: dict[str, dict[str, str]] = {}
    for pos, raw_input in enumerate(_list(definition["inputs"], "inputs")):
        item = validate_artifact(root, raw_input, f"inputs[{pos}]", INPUT_KINDS)
        if item["name"] in inputs:
            raise StateError(f"duplicate input name: {item['name']}")
        inputs[item["name"]] = item

    raw_steps = _list(definition["steps"], "steps")
    if not raw_steps:
        raise StateError("steps must not be empty")
    steps: dict[str, dict[str, Any]] = {}
    for pos, value in enumerate(raw_steps):
        step = _object(value, f"steps[{pos}]")
        if set(step) != {"step_id", "depends_on", "input_names"}:
            raise StateError(f"steps[{pos}] has an unexpected shape")
        step_id = _text(step["step_id"], f"steps[{pos}].step_id")
        if not ID.fullmatch(step_id):
            raise StateError(f"step_id must match {ID.pattern}")
        if step_id in steps:
            raise StateError(f"duplicate step_id: {step_id}")
        depends_on = _list(step["depends_on"], f"steps[{pos}].depends_on")
        input_names = _list(step["input_names"], f"steps[{pos}].input_names")
        if any(not isinstance(item, str) for item in [*depends_on, *input_names]):
            raise StateError("step dependencies and inputs must be strings")
        if len(set(depends_on)) != len(depends_on) or len(set(input_names)) != len(input_names):
            raise StateError(f"steps[{pos}] contains duplicate dependencies or inputs")
        unknown_inputs = set(input_names) - set(inputs)
        if unknown_inputs:
            raise StateError(f"{step_id} names unknown inputs: {sorted(unknown_inputs)}")
        steps[step_id] = {
            "depends_on": depends_on,
            "input_names": input_names,
            "state": "pending",
            "attempts": [],
            "outputs": [],
            "receipt": None,
            "reason": None,
            "invalidated_by": [],
            "started_at": None,
            "completed_at": None,
        }
    for step_id, step in steps.items():
        unknown = set(step["depends_on"]) - set(steps)
        if unknown:
            raise StateError(f"{step_id} names unknown dependencies: {sorted(unknown)}")
        if step_id in step["depends_on"]:
            raise StateError(f"{step_id} cannot depend on itself")

    # A complete topological walk rejects indirect dependency cycles.
    ready: set[str] = set()
    remaining = set(steps)
    while remaining:
        available = {name for name in remaining if set(steps[name]["depends_on"]) <= ready}
        if not available:
            raise StateError("step dependency graph contains a cycle")
        ready |= available
        remaining -= available

    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_type": workflow_type,
        "workflow_version": _text(definition["workflow_version"], "workflow_version"),
        "target": _text(definition["target"], "target"),
        "linked_ids": {key: value.strip() for key, value in linked_ids.items()},
        "inputs": inputs,
        "steps": steps,
        "next_action": _text(definition["next_action"], "next_action"),
        "unresolved_questions": [value.strip() for value in questions],
    }


def workflows_root(root: Path) -> Path:
    return root / ".state" / "workflows"


def checkpoint_path(root: Path, workflow_id: str) -> Path:
    if not WORKFLOW_ID.fullmatch(workflow_id):
        raise StateError("workflow id must look like WF-YYYYMMDD-xxxxxxxx")
    return workflows_root(root) / workflow_id / "checkpoint.json"


def read_checkpoint(root: Path, workflow_id: str) -> dict[str, Any]:
    path = checkpoint_path(root, workflow_id)
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StateError(f"unknown workflow {workflow_id}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError(f"checkpoint is unreadable or invalid: {exc}") from None
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise StateError("unsupported checkpoint schema")
    return checkpoint


def write_checkpoint(root: Path, checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at"] = now_utc()
    path = checkpoint_path(root, checkpoint["workflow_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n")


def initialize(root: Path, raw: Any, requested_id: str | None = None) -> tuple[dict[str, Any], bool]:
    definition = validate_definition(root, raw)
    definition_digest = value_digest({
        key: definition[key] for key in ("workflow_type", "workflow_version", "target", "linked_ids", "inputs", "steps")
    })
    workflow_id = requested_id or f"WF-{dt.date.today():%Y%m%d}-{definition_digest[:8]}"
    if not WORKFLOW_ID.fullmatch(workflow_id):
        raise StateError("workflow id must look like WF-YYYYMMDD-xxxxxxxx")
    with exclusive_lock(workflows_root(root) / ".lock"):
        path = checkpoint_path(root, workflow_id)
        if path.exists():
            existing = read_checkpoint(root, workflow_id)
            if existing.get("definition_digest") != definition_digest:
                raise StateError(f"workflow id collision: {workflow_id}")
            return existing, True
        stamp = now_utc()
        checkpoint = definition | {
            "workflow_id": workflow_id,
            "definition_digest": definition_digest,
            "created_at": stamp,
            "updated_at": stamp,
            "history": [{"at": stamp, "event": "initialized"}],
        }
        write_checkpoint(root, checkpoint)
        return checkpoint, False


def descendants(checkpoint: dict[str, Any], seeds: set[str]) -> set[str]:
    affected = set(seeds)
    changed = True
    while changed:
        changed = False
        for step_id, step in checkpoint["steps"].items():
            if step_id not in affected and set(step["depends_on"]) & affected:
                affected.add(step_id)
                changed = True
    return affected


def invalidate_steps(checkpoint: dict[str, Any], seeds: set[str], reason: str) -> set[str]:
    affected = descendants(checkpoint, seeds)
    stamp = now_utc()
    for step_id in affected:
        step = checkpoint["steps"][step_id]
        step["state"] = "invalidated"
        step["reason"] = reason
        step["invalidated_by"] = sorted(seeds)
        step["completed_at"] = None
    checkpoint["history"].append({"at": stamp, "event": "invalidated",
                                  "steps": sorted(affected), "reason": reason})
    checkpoint["next_action"] = f"Review and rerun invalidated step(s): {', '.join(sorted(affected))}"
    return affected


def start_step(root: Path, workflow_id: str, step_id: str,
               expected_receipt: str | None) -> dict[str, Any]:
    with exclusive_lock(workflows_root(root) / ".lock"):
        checkpoint = read_checkpoint(root, workflow_id)
        step = checkpoint["steps"].get(step_id)
        if not isinstance(step, dict):
            raise StateError(f"unknown step {step_id}")
        if step["state"] == "running":
            raise StateError(f"{step_id} is already running; reconcile it before retrying")
        if step["state"] == "completed":
            raise StateError(f"{step_id} is already completed")
        incomplete = [name for name in step["depends_on"]
                      if checkpoint["steps"][name]["state"] != "completed"]
        if incomplete:
            raise StateError(f"{step_id} has incomplete dependencies: {', '.join(incomplete)}")
        receipt_ref = None
        if expected_receipt:
            receipt_path = safe_relative(root, expected_receipt, "expected_receipt")
            receipt_ref = receipt_path.relative_to(root.resolve()).as_posix()
        attempt_number = len(step["attempts"]) + 1
        operation_id = f"{workflow_id}:{step_id}:{attempt_number}"
        stamp = now_utc()
        step.update(state="running", started_at=stamp, completed_at=None, reason=None,
                    invalidated_by=[], outputs=[], receipt=None)
        step["attempts"].append({"attempt": attempt_number, "operation_id": operation_id,
                                 "started_at": stamp, "ended_at": None,
                                 "outcome": None, "expected_receipt": receipt_ref})
        checkpoint["history"].append({"at": stamp, "event": "started", "step": step_id,
                                      "operation_id": operation_id})
        checkpoint["next_action"] = f"Complete or reconcile {step_id} ({operation_id})"
        write_checkpoint(root, checkpoint)
        return {"workflow_id": workflow_id, "step_id": step_id, "operation_id": operation_id,
                "expected_receipt": receipt_ref}


def validate_receipt(root: Path, raw: Any, operation_id: str) -> dict[str, Any]:
    receipt = _object(raw, "receipt")
    if set(receipt) != {"operation_id", "owner", "committed_at", "outputs"}:
        raise StateError("receipt must contain exactly operation_id, owner, committed_at, outputs")
    if receipt["operation_id"] != operation_id:
        raise StateError("receipt operation_id does not match the running attempt")
    outputs = [validate_artifact(root, value, f"receipt.outputs[{pos}]", OUTPUT_KINDS)
               for pos, value in enumerate(_list(receipt["outputs"], "receipt.outputs"))]
    names = [output["name"] for output in outputs]
    if len(names) != len(set(names)):
        raise StateError("receipt output names must be unique")
    return {"operation_id": operation_id, "owner": _text(receipt["owner"], "receipt.owner"),
            "committed_at": _timestamp(receipt["committed_at"], "receipt.committed_at"),
            "outputs": outputs}


def complete_step(root: Path, workflow_id: str, step_id: str, receipt_raw: Any,
                  next_action: str) -> dict[str, Any]:
    with exclusive_lock(workflows_root(root) / ".lock"):
        checkpoint = read_checkpoint(root, workflow_id)
        step = checkpoint["steps"].get(step_id)
        if not isinstance(step, dict):
            raise StateError(f"unknown step {step_id}")
        if step["state"] != "running" or not step["attempts"]:
            raise StateError(f"{step_id} must be running before it can complete")
        attempt = step["attempts"][-1]
        receipt = validate_receipt(root, receipt_raw, attempt["operation_id"])
        stamp = now_utc()
        step.update(state="completed", outputs=receipt["outputs"], receipt=receipt,
                    completed_at=stamp, reason=None)
        attempt.update(ended_at=stamp, outcome="completed")
        checkpoint["history"].append({"at": stamp, "event": "completed", "step": step_id,
                                      "operation_id": attempt["operation_id"]})
        checkpoint["next_action"] = _text(next_action, "next_action")
        write_checkpoint(root, checkpoint)
        return step


def stop_step(root: Path, workflow_id: str, step_id: str, state: str,
              reason: str, next_action: str) -> dict[str, Any]:
    if state not in ("failed", "blocked"):
        raise StateError("stop state must be failed or blocked")
    with exclusive_lock(workflows_root(root) / ".lock"):
        checkpoint = read_checkpoint(root, workflow_id)
        step = checkpoint["steps"].get(step_id)
        if not isinstance(step, dict):
            raise StateError(f"unknown step {step_id}")
        if step["state"] != "running" or not step["attempts"]:
            raise StateError(f"{step_id} must be running before it can become {state}")
        stamp = now_utc()
        reason = _text(reason, "reason")
        step.update(state=state, reason=reason)
        step["attempts"][-1].update(ended_at=stamp, outcome=state)
        checkpoint["history"].append({"at": stamp, "event": state, "step": step_id,
                                      "reason": reason})
        checkpoint["next_action"] = _text(next_action, "next_action")
        write_checkpoint(root, checkpoint)
        return step


def invalidate_input(root: Path, workflow_id: str, input_name: str,
                     new_digest: str, reason: str, new_reference: str | None = None) -> dict[str, Any]:
    if not SHA256.fullmatch(new_digest):
        raise StateError("new digest must be a lowercase SHA-256 digest")
    with exclusive_lock(workflows_root(root) / ".lock"):
        checkpoint = read_checkpoint(root, workflow_id)
        item = checkpoint["inputs"].get(input_name)
        if not isinstance(item, dict):
            raise StateError(f"unknown input {input_name}")
        reference = new_reference.strip() if isinstance(new_reference, str) and new_reference.strip() else item["reference"]
        if item["digest"] == new_digest and item["reference"] == reference:
            return {"changed": False, "affected_steps": []}
        if item["kind"] == "file":
            actual = file_digest(safe_relative(root, reference, "input.reference"))
            if actual != new_digest:
                raise StateError("new digest does not match the input file")
        elif item["kind"] == "value" and value_digest(reference) != new_digest:
            raise StateError("new digest does not match the input value")
        old_digest = item["digest"]
        old_reference = item["reference"]
        item["digest"] = new_digest
        item["reference"] = reference
        seeds = {step_id for step_id, step in checkpoint["steps"].items()
                 if input_name in step["input_names"]}
        affected = invalidate_steps(checkpoint, seeds,
                                    f"input {input_name} changed: {reason}")
        checkpoint["history"].append({"at": now_utc(), "event": "input-changed",
                                      "input": input_name, "old_digest": old_digest,
                                      "new_digest": new_digest, "old_reference": old_reference,
                                      "new_reference": reference})
        write_checkpoint(root, checkpoint)
        return {"changed": True, "affected_steps": sorted(affected)}


def reconcile(root: Path, workflow_id: str) -> dict[str, Any]:
    """Validate file artifacts and finish running steps whose owner receipt exists."""
    with exclusive_lock(workflows_root(root) / ".lock"):
        checkpoint = read_checkpoint(root, workflow_id)
        reconciled: list[str] = []
        invalid_seeds: set[str] = set()
        reasons: list[str] = []

        for input_name, item in checkpoint["inputs"].items():
            if item["kind"] != "file":
                continue
            path = safe_relative(root, item["reference"], f"input {input_name}")
            if not path.is_file() or file_digest(path) != item["digest"]:
                seeds = {name for name, step in checkpoint["steps"].items()
                         if input_name in step["input_names"]}
                invalid_seeds |= seeds
                reasons.append(f"input {input_name} is missing or changed")

        for step_id, step in checkpoint["steps"].items():
            if step["state"] == "completed":
                for output in step["outputs"]:
                    if output["kind"] == "file":
                        path = safe_relative(root, output["reference"], f"output {output['name']}")
                        if not path.is_file() or file_digest(path) != output["digest"]:
                            invalid_seeds.add(step_id)
                            reasons.append(f"output {output['name']} for {step_id} is missing or changed")
            elif step["state"] == "running" and step["attempts"]:
                attempt = step["attempts"][-1]
                reference = attempt.get("expected_receipt")
                if reference:
                    receipt_path = safe_relative(root, reference, "expected receipt")
                    if receipt_path.is_file():
                        try:
                            raw = json.loads(receipt_path.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise StateError(f"expected receipt is unreadable or invalid: {exc}") from None
                        receipt = validate_receipt(root, raw, attempt["operation_id"])
                        stamp = now_utc()
                        step.update(state="completed", outputs=receipt["outputs"], receipt=receipt,
                                    completed_at=stamp, reason=None)
                        attempt.update(ended_at=stamp, outcome="completed")
                        checkpoint["history"].append({"at": stamp, "event": "receipt-reconciled",
                                                      "step": step_id,
                                                      "operation_id": attempt["operation_id"]})
                        reconciled.append(step_id)
        affected: set[str] = set()
        if invalid_seeds:
            affected = invalidate_steps(checkpoint, invalid_seeds, "; ".join(sorted(set(reasons))))
        if reconciled and not affected:
            checkpoint["next_action"] = "Continue from the next pending eligible step."
        write_checkpoint(root, checkpoint)
        return {"reconciled_steps": sorted(reconciled), "invalidated_steps": sorted(affected),
                "running_steps": sorted(name for name, step in checkpoint["steps"].items()
                                        if step["state"] == "running")}


def summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
    eligible = []
    for step_id, step in checkpoint["steps"].items():
        if step["state"] in ("pending", "failed", "invalidated") and all(
                checkpoint["steps"][dep]["state"] == "completed" for dep in step["depends_on"]):
            eligible.append(step_id)
    return {
        "workflow_id": checkpoint["workflow_id"],
        "workflow_type": checkpoint["workflow_type"],
        "target": checkpoint["target"],
        "updated_at": checkpoint["updated_at"],
        "steps": {name: {"state": step["state"], "reason": step["reason"],
                          "attempts": len(step["attempts"])}
                  for name, step in checkpoint["steps"].items()},
        "eligible_steps": eligible,
        "blocked_steps": sorted(name for name, step in checkpoint["steps"].items()
                                if step["state"] == "blocked"),
        "unresolved_questions": checkpoint["unresolved_questions"],
        "next_action": checkpoint["next_action"],
    }


def list_status(root: Path, target: str | None) -> list[dict[str, Any]]:
    results = []
    for path in sorted(workflows_root(root).glob("WF-*/checkpoint.json")):
        checkpoint = read_checkpoint(root, path.parent.name)
        if target is None or checkpoint.get("target") == target or target in checkpoint.get("linked_ids", {}).values():
            results.append(summary(checkpoint))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--definition", type=Path, required=True)
    init.add_argument("--workflow-id")
    start = sub.add_parser("start")
    start.add_argument("--workflow", required=True)
    start.add_argument("--step", required=True)
    start.add_argument("--expected-receipt")
    complete = sub.add_parser("complete")
    complete.add_argument("--workflow", required=True)
    complete.add_argument("--step", required=True)
    complete.add_argument("--receipt", type=Path, required=True)
    complete.add_argument("--next-action", required=True)
    stop = sub.add_parser("stop")
    stop.add_argument("--workflow", required=True)
    stop.add_argument("--step", required=True)
    stop.add_argument("--state", choices=("failed", "blocked"), required=True)
    stop.add_argument("--reason", required=True)
    stop.add_argument("--next-action", required=True)
    invalidate = sub.add_parser("invalidate-input")
    invalidate.add_argument("--workflow", required=True)
    invalidate.add_argument("--input", required=True)
    invalidate.add_argument("--digest", required=True)
    invalidate.add_argument("--reference")
    invalidate.add_argument("--reason", required=True)
    status = sub.add_parser("status")
    status_group = status.add_mutually_exclusive_group()
    status_group.add_argument("--workflow")
    status_group.add_argument("--target")
    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--workflow", required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "init":
            checkpoint, reused = initialize(root, load_json(root, args.definition, "definition"), args.workflow_id)
            output = {"workflow_id": checkpoint["workflow_id"], "reused": reused,
                      "next_action": checkpoint["next_action"]}
        elif args.command == "start":
            output = start_step(root, args.workflow, args.step, args.expected_receipt)
        elif args.command == "complete":
            output = complete_step(root, args.workflow, args.step,
                                   load_json(root, args.receipt, "receipt"), args.next_action)
        elif args.command == "stop":
            output = stop_step(root, args.workflow, args.step, args.state, args.reason, args.next_action)
        elif args.command == "invalidate-input":
            output = invalidate_input(root, args.workflow, args.input, args.digest, args.reason,
                                      args.reference)
        elif args.command == "reconcile":
            output = reconcile(root, args.workflow)
        elif args.workflow:
            output = summary(read_checkpoint(root, args.workflow))
        else:
            output = list_status(root, args.target)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except StateError as exc:
        print(f"workflow_state: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"workflow_state: filesystem error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
