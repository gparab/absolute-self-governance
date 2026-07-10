import argparse
import os
import sys
import json

# tracing.py decides console-vs-OTLP export at import time based on TESTING,
# and gets pulled in transitively via nudger -> consensus -> tracing below,
# before argparse has parsed a subcommand. Peek argv directly so `demo` (a
# zero-setup, first-impression command) doesn't spam raw span JSON.
if "demo" in sys.argv:
    os.environ.setdefault("TESTING", "True")

from self_governance.nudger import ContinuousNudger, write_swarm_config_to_stream
from self_governance.dimensioning import dimension_swarm
from self_governance.config import OrchestratorConfig
from self_governance.dashboard import display_dashboard


def main():
    parser = argparse.ArgumentParser(description="Absolute Self-Governance CLI")
    parser.add_argument("--config", help="Path to config YAML file")
    parser.add_argument(
        "--json-logs", action="store_true", help="Format output logs in structured JSON"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # run-nudger subcommand
    parser_run = subparsers.add_parser("run-nudger", help="Start the continuous nudger")
    parser_run.add_argument(
        "--workdir", default=".", help="Working directory (default: '.')"
    )

    # trigger-succession subcommand
    parser_trigger = subparsers.add_parser(
        "trigger-succession", help="Manually run succession"
    )
    parser_trigger.add_argument("--handoff", required=True, help="Path to handoff file")
    parser_trigger.add_argument(
        "--workdir", default=".", help="Working directory (default: '.')"
    )

    # dimension subcommand
    parser_dim = subparsers.add_parser(
        "dimension", help="Output serialized JSON swarm configuration"
    )
    parser_dim.add_argument(
        "-r", "--requirements", required=True, help="Requirements as a JSON string"
    )
    parser_dim.add_argument(
        "-m", "--matrix", required=True, help="Matrix as a JSON string"
    )

    # stats subcommand
    parser_stats = subparsers.add_parser("stats", help="Show the metrics dashboard")
    parser_stats.add_argument(
        "--watch", action="store_true", help="Repaint the dashboard every 2 seconds"
    )

    # dev subcommand: nudger + local monitoring server in one command
    parser_dev = subparsers.add_parser(
        "dev", help="Watch the current directory and serve a local status page"
    )
    parser_dev.add_argument(
        "--workdir", default=".", help="Working directory (default: '.')"
    )
    parser_dev.add_argument(
        "--port", type=int, default=8642, help="Monitor port (default: 8642)"
    )

    # benchmark subcommand
    parser_bench = subparsers.add_parser(
        "benchmark", help="Run the diagnostic comparison benchmark suite"
    )
    parser_bench.add_argument(
        "--reps", type=int, default=1,
        help="Repetitions per task per mode (default: 1, sequential). "
             ">1 runs the concurrent, process-isolated sweep instead.",
    )
    parser_bench.add_argument(
        "--workers", type=int, default=4,
        help="Concurrent workers for --reps > 1 (default: 4, max 16).",
    )

    # demo subcommand: zero-setup, zero-cost walkthrough of dynamic team sizing
    parser_demo = subparsers.add_parser(
        "demo", help="Zero-setup demo of dynamic swarm sizing (no API key needed)"
    )
    parser_demo.add_argument(
        "--port", type=int, default=8643, help="Monitor port (default: 8643)"
    )

    args = parser.parse_args()
    from self_governance.telemetry import setup_telemetry, new_correlation_id

    setup_telemetry(json_logging=args.json_logs)
    new_correlation_id()

    config = OrchestratorConfig(args.config)

    if args.subcommand == "run-nudger":
        import signal

        nudger = ContinuousNudger(working_directory=args.workdir, config=config)
        # k8s/systemd stop sends SIGTERM: exit cleanly instead of mid-succession
        signal.signal(signal.SIGTERM, lambda *_: nudger.stop())
        nudger.watch_handoff()
    elif args.subcommand == "trigger-succession":
        with open(args.handoff, "r", encoding="utf-8") as f:
            content = f.read()
        nudger = ContinuousNudger(working_directory=args.workdir, config=config)
        nudger.trigger_succession(content)
    elif args.subcommand == "dimension":
        req = json.loads(args.requirements)
        mat = json.loads(args.matrix)
        swarm_config = dimension_swarm(req, mat)
        write_swarm_config_to_stream(sys.stdout, swarm_config)
        sys.stdout.write("\n")
    elif args.subcommand == "stats":
        if args.watch:
            import time

            try:
                while True:
                    sys.stdout.write("\033[2J\033[H")  # clear screen, home cursor
                    display_dashboard()
                    sys.stdout.flush()
                    time.sleep(2)
            except KeyboardInterrupt:
                pass
        else:
            display_dashboard()
    elif args.subcommand == "dev":
        import signal
        import threading
        import uvicorn
        from self_governance.devserver import dev_app

        # Monitor server on localhost only — it exposes cost/usage data.
        server = uvicorn.Server(
            uvicorn.Config(dev_app, host="127.0.0.1", port=args.port, log_level="warning")
        )
        threading.Thread(target=server.run, daemon=True).start()

        nudger = ContinuousNudger(working_directory=args.workdir, config=config)
        signal.signal(signal.SIGTERM, lambda *_: nudger.stop())
        handoff = config.handoff_file
        print(f"ASG dev mode: watching {args.workdir}/{handoff}")
        print(f"Monitor:      http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
        try:
            nudger.watch_handoff()
        except KeyboardInterrupt:
            nudger.stop()
    elif args.subcommand == "demo":
        import threading
        import uvicorn
        from self_governance.devserver import dev_app
        from self_governance.demo import run_demo

        server = uvicorn.Server(
            uvicorn.Config(dev_app, host="127.0.0.1", port=args.port, log_level="warning")
        )
        threading.Thread(target=server.run, daemon=True).start()

        print("ASG demo: no API key required, zero cost, zero setup.")
        print(f"Dashboard:    http://127.0.0.1:{args.port}/  (open this in a browser)")
        run_demo()
        print("\nDashboard is still live. Press Ctrl-C to exit.")
        try:
            while True:
                import time

                time.sleep(1)
        except KeyboardInterrupt:
            pass
    elif args.subcommand == "benchmark":
        if args.reps <= 1:
            from self_governance.benchmark import run_benchmark

            results = run_benchmark()
            print(
                f"\n{'Task Name':<30} | {'Baseline (Pass/Time/Cost)':<30} | {'ASG Mode (Pass/Time/Cost)':<30}"
            )
            print("-" * 96)
            for task_id, metric in results.items():
                b = metric["baseline"]
                a = metric["asg"]
                b_str = f"{'PASS' if b['passed'] else 'FAIL'} / {b['latency_sec']}s / ${b['estimated_cost_usd']:.5f}"
                a_str = f"{'PASS' if a['passed'] else 'FAIL'} / {a['latency_sec']}s / ${a['estimated_cost_usd']:.5f}"
                print(f"{metric['name']:<30} | {b_str:<30} | {a_str:<30}")
        else:
            from self_governance.benchmark import run_benchmark_parallel

            done = {"n": 0}

            def _progress(outcome):
                done["n"] += 1
                r = outcome["result"]
                status = "PASS" if r.get("passed") else "FAIL"
                print(
                    f"[{done['n']}] {outcome['task_id']} {outcome['mode']} "
                    f"rep {outcome['rep']+1}/{args.reps}: {status}"
                )

            print(
                f"Running {args.reps} reps/task/mode with {args.workers} "
                f"concurrent workers (each in its own isolated tempdir)...\n"
            )
            results = run_benchmark_parallel(
                reps=args.reps, workers=args.workers, on_result=_progress
            )

            print(f"\n{'Task':<24} {'Mode':<9} {'Pass':<8} {'MeanLat':<9} {'MeanCost':<10}")
            print("-" * 62)
            for task_id, data in results.items():
                for mode in ("baseline", "asg"):
                    runs = data[mode]
                    n = len(runs)
                    passed = sum(1 for r in runs if r.get("passed"))
                    mean_lat = sum(r.get("latency_sec", 0) for r in runs) / n
                    mean_cost = sum(r.get("estimated_cost_usd", 0) for r in runs) / n
                    print(
                        f"{task_id:<24} {mode:<9} {passed}/{n:<6} "
                        f"{mean_lat:<9.1f} ${mean_cost:<9.6f}"
                    )


if __name__ == "__main__":
    main()
