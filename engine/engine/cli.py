"""`engine` CLI — headless parity with the API.

  engine run --cluster <id|name> --scope all --window 7d --resources cpu,memory [--collect-data] [--ttl 24h]
  engine run --type maintenance --cluster <id|name> --app <ns/Kind/name> --duration 30m --deadline 3d
  engine serve [--host 0.0.0.0 --port 8000]
  engine init-db            # dev: create the SQLite schema from the canonical migrations
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .analysis_core.io.statestore import StateStore
from .runner import run_analysis
from .synth import export_csv, export_json, seed_cluster, synthetic_cluster


def _add_db_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db-driver", default="sqlite", help="sqlite|postgres")
    p.add_argument("--db-dsn", default="./kubesiesta.db", help="connection string / sqlite path")


def _open_store(args) -> StateStore:
    return StateStore(driver=args.db_driver, dsn=args.db_dsn)


def cmd_run(args) -> int:
    store = _open_store(args)
    try:
        overrides = {}
        if args.resources:
            overrides["resources"] = [r.strip() for r in args.resources.split(",") if r.strip()]
        if args.window:
            overrides["window"] = args.window
        if getattr(args, "resample_freq", None):
            overrides["resample_freq"] = args.resample_freq
        cluster: object = int(args.cluster) if args.cluster and args.cluster.isdigit() else (args.cluster or "default")

        kwargs = {}
        if args.type == "maintenance":
            if not args.app:
                print("error: --type maintenance requires --app <workload_uid>", file=sys.stderr)
                return 2
            if not args.duration or not args.deadline:
                print("error: --type maintenance requires --duration and --deadline", file=sys.stderr)
                return 2
            kwargs.update(
                target_workload_uid=args.app,
                duration=args.duration,
                deadline=args.deadline,
            )
        result = run_analysis(
            store,
            cluster=cluster,
            scope=args.scope,
            config_overrides=overrides,
            ttl_hours=_ttl_hours(args.ttl),
            collect_data=args.collect_data,
            name=args.name,
            run_type=args.type,
            **kwargs,
        )
    finally:
        store.close()

    if args.type == "maintenance":
        print(json.dumps({
            "run_id": result.run_id,
            "name": result.name,
            "status": result.status,
            "recommended_start": result.recommended_start,
            "recommended_end": result.recommended_end,
            "max_score": result.max_score,
            "data_as_of": result.data_as_of,
            "stale": result.stale,
        }, indent=2))
    else:
        print(json.dumps({
            "run_id": result.run_id,
            "name": result.name,
            "status": result.status,
            "recommendations": result.recommendations,
            "data_as_of": result.data_as_of,
            "stale": result.stale,
        }, indent=2))
    return 0


def cmd_serve(args) -> int:
    import os
    import uvicorn
    # Respect env already set by the environment (e.g. a Kubernetes Deployment); only
    # fall back to the CLI flags when the env isn't set, so flag defaults don't clobber it.
    os.environ.setdefault("KUBESIESTA_DB_DRIVER", args.db_driver)
    os.environ.setdefault("KUBESIESTA_DB_DSN", args.db_dsn)
    uvicorn.run("engine.api.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_init_db(args) -> int:
    store = _open_store(args)
    try:
        store.apply_schema()
        print(f"schema ready in {args.db_dsn}")
    finally:
        store.close()
    return 0


def cmd_synth(args) -> int:
    cluster = synthetic_cluster(seed=args.seed)
    if args.format == "csv":
        out = args.out or "fixtures"
        paths = export_csv(cluster, out)
        print(f"wrote CSV fixtures: {', '.join(paths.values())}")
    else:
        out = args.out or "fixtures/cluster.json"
        import os
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        export_json(cluster, out)
        print(f"wrote JSON fixture: {out}")
    print(f"known candidates: {sorted(cluster.candidate_uids)}")

    if args.seed_db:
        store = _open_store(args)
        try:
            if args.db_driver == "sqlite":
                store.apply_schema()  # dev convenience; Postgres schema comes from `collector db migrate`
            cid = seed_cluster(store, cluster)
            print(f"seeded cluster '{cluster.name}' (id={cid}) into {args.db_dsn}")
        finally:
            store.close()
    return 0


def _ttl_hours(ttl: Optional[str]) -> int:
    if not ttl:
        return 24
    s = ttl.strip().lower()
    try:
        if s.endswith("h"):
            return int(float(s[:-1]))
        if s.endswith("d"):
            return int(float(s[:-1]) * 24)
        return int(float(s))
    except ValueError:
        return 24


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine", description="Kube Siesta — core engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="analyze stored data and write recommendations")
    run.add_argument("--type", choices=["job", "maintenance"], default="job",
                     help="which recommender head to run (default: job)")
    run.add_argument("--cluster", default="default", help="cluster id or name")
    run.add_argument("--scope", default="all", help="'all' or comma-separated workload_uids")
    run.add_argument("--window", default="7d")
    run.add_argument("--resources", default="cpu,memory")
    run.add_argument("--resample-freq", default=None,
                     help="pandas resample freq (e.g. '1h', '2min'); overrides settings")
    run.add_argument("--collect-data", action="store_true", help="(job only) trigger a fresh collection first")
    run.add_argument("--ttl", default="24h")
    run.add_argument("--name", default=None, help="override the generated run name")
    # --- maintenance-only ---
    run.add_argument("--app", default=None,
                     help="(maintenance) target workload_uid, e.g. 'ns/Deployment/name'")
    run.add_argument("--duration", default=None,
                     help="(maintenance) downtime length, e.g. '30m', '2h', '1d'")
    run.add_argument("--deadline", default=None,
                     help="(maintenance) window must end by this time — '3d' (relative) or ISO-8601")
    _add_db_flags(run)
    run.set_defaults(func=cmd_run)

    serve = sub.add_parser("serve", help="run the FastAPI app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    _add_db_flags(serve)
    serve.set_defaults(func=cmd_serve)

    init = sub.add_parser("init-db", help="create the SQLite schema (dev)")
    _add_db_flags(init)
    init.set_defaults(func=cmd_init_db)

    synth = sub.add_parser("synth", help="write synthetic fixtures (CSV/JSON), optionally seed the DB")
    synth.add_argument("--format", choices=["json", "csv"], default="json")
    synth.add_argument("--out", default=None, help="output path (file for json, dir for csv)")
    synth.add_argument("--seed", type=int, default=0)
    synth.add_argument("--seed-db", action="store_true", help="also seed the fixture into the DB")
    _add_db_flags(synth)
    synth.set_defaults(func=cmd_synth)

    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    # `--scope all` stays the string "all"; a CSV becomes a workload_uids filter.
    if getattr(args, "scope", None) and args.scope != "all" and "," in args.scope:
        args.scope = {"workload_uids": [s.strip() for s in args.scope.split(",") if s.strip()]}
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
