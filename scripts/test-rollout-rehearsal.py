#!/usr/bin/env python3
"""Deploy and verify the isolated HOU1 test rollout.

The deploy phase intentionally accepts only test inventory targets and immutable
source references.  Recovery drills are operator controlled; the verify phase
records hashes and sizes of their redacted evidence without copying contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit


SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_EVIDENCE = (
    "dns_tls",
    "monitoring",
    "password_migration",
    "authentik_outage",
    "restore",
    "netbird_rollback",
    "break_glass",
)


class RolloutError(ValueError):
    """A rollout was rejected before it could touch a host."""


def reject_production(value: str, label: str) -> None:
    lowered = value.lower()
    if any(token in lowered for token in ("prod", "production")):
        raise RolloutError(f"production {label} is forbidden in the test rehearsal")


def validate_sha(value: str, label: str) -> str:
    if not SHA.fullmatch(value):
        raise RolloutError(f"{label} must be a 40-character immutable commit SHA")
    return value


def validate_config(args: argparse.Namespace) -> None:
    if args.environment != "test":
        raise RolloutError("--environment must be test")
    for label, value in (("infra ref", args.infra_ref), ("staff ref", args.staff_ref),
                         ("NetBird ref", args.netbird_ref)):
        validate_sha(value, label)
        reject_production(value, label)
    for label, value in (("test domain", args.test_domain), ("issuer", args.issuer),
                         ("NetBird URL", args.netbird_url), ("certificate ref", args.cert_ref),
                         ("secret ref", args.secret_ref)):
        reject_production(value, label)
    if "test" not in args.test_domain.lower():
        raise RolloutError("--test-domain must identify the isolated test domain")
    for label, value in (("issuer", args.issuer), ("NetBird URL", args.netbird_url)):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or "test" not in parsed.netloc.lower():
            raise RolloutError(f"{label} must be an HTTPS test endpoint")
    if args.inventory != "ansible/inventory/inventory.yml":
        raise RolloutError("the rehearsal is pinned to the repository inventory")


def deployment_commands(args: argparse.Namespace) -> list[list[str]]:
    common = ["-i", args.inventory, "--limit", "hou1_test", "-e", "target_hosts=hou1_test",
              "-e", "deploy_environment=test", "-e", f"rollout_ref={args.infra_ref}"]
    root = Path("ansible/playbooks")
    return [
        ["ansible-playbook", *common, str(root / "authentik-preflight.yml")],
        ["ansible-playbook", *common, str(root / "hou1.yml")],
        ["ansible-playbook", *common, str(root / "authentik.yml")],
        ["ansible-playbook", *common, str(root / "authentik-bootstrap.yml")],
    ]


def git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def run_deploy(args: argparse.Namespace, evidence_dir: Path) -> list[dict[str, object]]:
    if git_head() != args.infra_ref:
        raise RolloutError("checkout HEAD must equal --infra-ref before deployment")
    outcomes: list[dict[str, object]] = []
    for index, command in enumerate(deployment_commands(args), start=1):
        started = time.monotonic()
        # Ansible output can contain operational details. Keep it out of the
        # redacted evidence directory and report only command/status metadata.
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        outcomes.append({"step": index, "command": command, "returncode": result.returncode,
                         "seconds": round(time.monotonic() - started, 2)})
        if result.returncode:
            break
    (evidence_dir / "deployment-status.json").write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8")
    if outcomes and outcomes[-1]["returncode"] != 0:
        raise RolloutError(f"deployment step {outcomes[-1]['step']} failed")
    return outcomes


def evidence_record(path: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size == 0:
        raise RolloutError(f"evidence file is missing or empty: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"sha256": digest, "bytes": path.stat().st_size}


def verify_evidence(args: argparse.Namespace, evidence_dir: Path) -> dict[str, object]:
    records = {}
    for name in REQUIRED_EVIDENCE:
        path = Path(getattr(args, f"{name}_evidence"))
        try:
            path.relative_to(evidence_dir)
        except ValueError as exc:
            raise RolloutError(f"{name} evidence must be inside --evidence-dir") from exc
        records[name] = evidence_record(path)
    report = {"environment": "test", "status": "passed", "evidence": records,
              "refs": {"infra": args.infra_ref, "staff": args.staff_ref, "netbird": args.netbird_ref,
                       "certificate": args.cert_ref, "secret": args.secret_ref},
              "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (evidence_dir / "rollout-rehearsal.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--phase", choices=("plan", "deploy", "verify"), default="plan")
    result.add_argument("--environment", default="test")
    result.add_argument("--infra-ref", required=True)
    result.add_argument("--staff-ref", required=True)
    result.add_argument("--netbird-ref", required=True)
    result.add_argument("--test-domain", required=True)
    result.add_argument("--issuer", required=True)
    result.add_argument("--netbird-url", required=True)
    result.add_argument("--cert-ref", required=True)
    result.add_argument("--secret-ref", required=True)
    result.add_argument("--inventory", default="ansible/inventory/inventory.yml")
    result.add_argument("--evidence-dir", type=Path, required=True)
    for name in REQUIRED_EVIDENCE:
        result.add_argument(f"--{name.replace('_', '-')}-evidence", dest=f"{name}_evidence")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_config(args)
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        if args.phase == "plan":
            print(json.dumps({"environment": "test", "refs": {"infra": args.infra_ref, "staff": args.staff_ref,
                             "netbird": args.netbird_ref}, "commands": deployment_commands(args)}, indent=2))
        elif args.phase == "deploy":
            run_deploy(args, args.evidence_dir)
        else:
            missing = [name for name in REQUIRED_EVIDENCE if not getattr(args, f"{name}_evidence")]
            if missing:
                raise RolloutError("missing evidence arguments: " + ", ".join(missing))
            verify_evidence(args, args.evidence_dir)
    except (RolloutError, subprocess.CalledProcessError) as exc:
        print(f"rollout rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
