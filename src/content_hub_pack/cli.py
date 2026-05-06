from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path

from content_hub_pack.workflows import (
    compose_daily_pack_stage,
    deliver_to_xteink_stage,
    ingest_hn_stage,
    ingest_substack_stage,
    ingest_youtube_stage,
    load_config_stage,
    run_pipeline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Content Hub Pack CLI")
    parser.add_argument("--project-root", default=".", help="Project root path")
    parser.add_argument("--config", default="config/config.yaml", help="Config file path")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level for live pipeline output",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in [
        "load-config",
        "ingest-substack",
        "ingest-youtube-playlist",
        "ingest-hn",
        "compose-daily-pack",
        "deliver-to-xteink",
        "run-pipeline",
    ]:
        sub = subparsers.add_parser(name)
        sub.add_argument("--run-date", help="Run date in YYYY-MM-DD")
        if name == "run-pipeline":
            sub.add_argument("--retry-only-if-needed", action="store_true")

    return parser


def _resolve(args: argparse.Namespace) -> tuple[Path, Path]:
    project_root = Path(args.project_root).resolve()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = project_root / config_path
    return project_root, config_path.resolve()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    project_root, config_path = _resolve(args)

    if args.command == "load-config":
        _, result = load_config_stage(project_root, config_path)
        print(json.dumps(asdict(result), indent=2))
        return

    if args.command == "run-pipeline":
        results = run_pipeline(
            project_root=project_root,
            config_path=config_path,
            run_date=args.run_date,
            retry_only_if_needed=args.retry_only_if_needed,
        )
        print(json.dumps([asdict(item) for item in results], indent=2))
        return

    settings, _ = load_config_stage(project_root, config_path)
    if not args.run_date:
        parser.error(f"{args.command} requires --run-date")

    if args.command == "ingest-substack":
        result = ingest_substack_stage(settings, args.run_date)
    elif args.command == "ingest-youtube-playlist":
        result = ingest_youtube_stage(settings, args.run_date)
    elif args.command == "ingest-hn":
        result = ingest_hn_stage(settings, args.run_date)
    elif args.command == "compose-daily-pack":
        result = compose_daily_pack_stage(settings, args.run_date)
    elif args.command == "deliver-to-xteink":
        result = deliver_to_xteink_stage(settings, args.run_date)
    else:
        parser.error(f"Unknown command: {args.command}")
        return

    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
