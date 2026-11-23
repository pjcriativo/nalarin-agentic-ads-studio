"""CLI.  python -m app.homologation plan | preflight | run --execute [--only OP ...] [--out report.json]

``plan`` and ``preflight`` are OFFLINE (no request is sent). ``run`` sends real requests only with ``--execute`` AND
HOMOLOG_AUTHORIZATION set to the phrase in guard.py."""
import argparse
import asyncio
import json
import os
import sys

from app.homologation import guard
from app.homologation import operations as O
from app.homologation.runner import Runner, _model_for


def _plan(env) -> int:
    from app.agents.registry import AGENTS
    print("Allowlisted operations (nothing is sent by `plan`):")
    for op in O.OPERATIONS.values():
        print(f"  {op.op_id:34} {op.kind:10} max_requests={op.max_calls}  {op.description}")
    print("\nAgents and the real-data tools they can call:")
    for key, a in AGENTS.items():
        tools = ", ".join(t for t in a.allowed_tools if t.startswith(("google_ads", "web."))) or "-"
        print(f"  {key:22} {tools}")
    return 0


def _preflight(env) -> int:
    print("Preflight (offline; values are never printed):")
    for op in O.OPERATIONS.values():
        missing = O.missing_env(op, env)
        print(f"  {'READY  ' if not missing else 'MISSING'} {op.op_id:34}" + (f" needs {', '.join(missing)}" if missing else ""))
    for p in O.AI_PROVIDERS:
        m = _model_for(p, env)
        print(f"  price {p:10} input={'set' if m.input_price_micros_per_mtok is not None else 'MISSING'} "
              f"output={'set' if m.output_price_micros_per_mtok is not None else 'MISSING'}")
    ledger = guard.CostLedger.shared(env)
    print(f"  aggregate ceiling (whole Phase B): US$ {ledger.max_total_micros / 1e6:.2f}; already recorded: US$ {ledger.spent_micros / 1e6:.4f}")
    print(f"  authorization phrase: {'present' if guard.authorized(env) else 'absent (real calls refused)'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.homologation")
    ap.add_argument("mode", choices=["plan", "preflight", "run", "scan", "redact"])
    ap.add_argument("--execute", action="store_true", help="really send the requests (also needs HOMOLOG_AUTHORIZATION)")
    ap.add_argument("--only", nargs="*", help="restrict to these operation ids")
    ap.add_argument("paths", nargs="*", help="(scan) files/directories to check for secrets")
    ap.add_argument("--values-only", action="store_true", help="(scan) match only configured secret values, not token-shaped patterns")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--max-total-usd", type=float, default=guard.DEFAULT_MAX_TOTAL_USD)
    ap.add_argument("--max-op-usd", type=float, default=guard.DEFAULT_MAX_OP_USD)
    args = ap.parse_args(argv)
    env = dict(os.environ)
    if args.mode == "plan":
        return _plan(env)
    if args.mode == "preflight":
        return _preflight(env)
    if args.mode == "redact":     # stdin -> stdout with every configured secret and token-shaped string masked
        redact = guard.Redactor(guard.secret_values(env))
        for line in sys.stdin:
            sys.stdout.write(redact(line.rstrip(chr(10))) + chr(10))
        return 0
    if args.mode == "scan":       # exit 1 when a secret value is found in the given paths (names printed, never values)
        hits = guard.scan_for_secrets(args.paths, env, patterns=not args.values_only)
        print(chr(10).join(f"SECRET FOUND IN: {h}" for h in hits) or "no secret found")
        return 1 if hits else 0
    try:
        guard.require_authorization(args.execute, env)
    except guard.NotAuthorized as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    ledger = guard.CostLedger.shared(env, args.max_total_usd, args.max_op_usd)
    runner = Runner(env, ledger, args.only)
    asyncio.run(runner.execute())
    text = runner.redact(json.dumps(runner.report(), indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0 if runner.report()["summary"][guard.FAILED] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
