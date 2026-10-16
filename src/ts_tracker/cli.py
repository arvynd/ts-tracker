import argparse

import polars as pl

from ts_tracker import hierarchy
from ts_tracker.bench import run_bench
from ts_tracker.config import load_config
from ts_tracker.ingest import eia
from ts_tracker.sample import make_sample


def cmd_data(args: argparse.Namespace) -> None:
    cfg = load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.data_dir / "sample.parquet"
    make_sample().write_parquet(path)
    print(f"wrote {path}")


def cmd_ingest(args: argparse.Namespace) -> None:
    eia.main(load_config())


def cmd_validate(args: argparse.Namespace) -> None:
    eia.validate_main(load_config(), args.snapshot)


def cmd_hierarchy(args: argparse.Namespace) -> None:
    hierarchy.main(load_config(), args.snapshot)


def cmd_bench(args: argparse.Namespace) -> None:
    cfg = load_config()
    df = pl.read_parquet(cfg.data_dir / "sample.parquet")
    results = run_bench(df, cfg)
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.results_dir / "smoke.parquet"
    results.write_parquet(path)
    print(results)
    print(f"wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ts-tracker")
    sub = parser.add_subparsers(required=True)
    sub.add_parser("data", help="write the 100-row sample panel").set_defaults(func=cmd_data)
    sub.add_parser("ingest", help="download EIA-930 raw snapshots").set_defaults(func=cmd_ingest)
    validate = sub.add_parser("validate", help="check stored raw files and write quality reports")
    validate.add_argument("--snapshot", help="raw snapshot date, e.g. 2026-10-14 (default: latest)")
    validate.set_defaults(func=cmd_validate)
    tree = sub.add_parser("hierarchy", help="build the hierarchy and summing matrix")
    tree.add_argument("--snapshot", help="raw snapshot date, e.g. 2026-10-14 (default: latest)")
    tree.set_defaults(func=cmd_hierarchy)
    sub.add_parser("bench", help="run the smoke benchmark").set_defaults(func=cmd_bench)
    args = parser.parse_args()
    args.func(args)
