"""`python -m demo_kit {run,seed,check}` from `backend/` (see the package docstring)."""

from __future__ import annotations

import argparse
import sys

import httpx

from demo_kit import preflight, seed, stack


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # progress shows at once even when piped (make, a log file)
    parser = argparse.ArgumentParser(prog="python -m demo_kit", description="SEHAT AI demo kit (docs/07)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="start the demo stack, seed it, check it; Ctrl-C stops it")
    run.add_argument("--provider", choices=("local", "fake"), default="local",
                     help="extraction model; 'fake' is a keyword stand-in for a rehearsal without the model")
    run.add_argument("--online", action="store_true", help="keep the developer cloud settings (the assistant beat needs Azure)")
    run.add_argument("--no-llm", action="store_true", help="the local model server is already running")
    run.add_argument("--keep-db", action="store_true", help="reuse the scratch database instead of starting fresh")
    run.add_argument("--api-port", type=int, default=8010)
    run.add_argument("--web-port", type=int, default=3010)
    for name, help_text in (("seed", "seed a running backend with the synthetic demo cases"), ("check", "the pre-demo check")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--api", default="http://127.0.0.1:8010")
        if name == "seed":
            p.add_argument("--allow-existing", action="store_true", help="seed even if the database already has cases")
        else:
            p.add_argument("--web", default="http://127.0.0.1:3010")
            p.add_argument("--provider", choices=("local", "fake"), default="local", help="the extraction model the demo expects")
    args = parser.parse_args(argv)

    if args.command == "run":
        return stack.run(provider=args.provider, online=args.online, with_llm=not args.no_llm, api_port=args.api_port,
                         web_port=args.web_port, fresh=not args.keep_db)
    try:
        api = seed.loopback_url(args.api)
        with httpx.Client(base_url=api, timeout=60) as client:
            if args.command == "seed":
                print("Seeding the synthetic demo cases:")
                seed.seed(client, allow_existing=args.allow_existing)
                return 0
            checks = preflight.run_checks(client, expect_provider=args.provider, web=seed.loopback_url(args.web))
    except seed.SeedRefused as exc:
        print(f"Refused: {exc}")
        return 2
    except httpx.HTTPError as exc:
        print(f"Cannot reach {args.api}: {type(exc).__name__}. Is the backend running?")
        return 1
    preflight.print_checks(checks)
    return preflight.exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
