import argparse
import sys
import json
from self_governance.nudger import ContinuousNudger, write_swarm_config_to_stream
from self_governance.dimensioning import dimension_swarm
from self_governance.config import OrchestratorConfig
from self_governance.dashboard import display_dashboard

def main():
    parser = argparse.ArgumentParser(description="Absolute Self-Governance CLI")
    parser.add_argument("--config", help="Path to config YAML file")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # run-nudger subcommand
    parser_run = subparsers.add_parser("run-nudger", help="Start the continuous nudger")
    parser_run.add_argument("--workdir", default=".", help="Working directory (default: '.')")

    # trigger-succession subcommand
    parser_trigger = subparsers.add_parser("trigger-succession", help="Manually run succession")
    parser_trigger.add_argument("--handoff", required=True, help="Path to handoff file")
    parser_trigger.add_argument("--workdir", default=".", help="Working directory (default: '.')")

    # dimension subcommand
    parser_dim = subparsers.add_parser("dimension", help="Output serialized JSON swarm configuration")
    parser_dim.add_argument("-r", "--requirements", required=True, help="Requirements as a JSON string")
    parser_dim.add_argument("-m", "--matrix", required=True, help="Matrix as a JSON string")

    # stats subcommand
    parser_stats = subparsers.add_parser("stats", help="Show the metrics dashboard")

    # benchmark subcommand
    parser_bench = subparsers.add_parser("benchmark", help="Run the diagnostic comparison benchmark suite")

    args = parser.parse_args()
    config = OrchestratorConfig(args.config)

    if args.subcommand == "run-nudger":
        nudger = ContinuousNudger(working_directory=args.workdir, config=config)
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
        display_dashboard()
    elif args.subcommand == "benchmark":
        from self_governance.benchmark import run_benchmark
        results = run_benchmark()
        print(f"\n{'Task Name':<30} | {'Baseline (Pass/Time/Cost)':<30} | {'ASG Mode (Pass/Time/Cost)':<30}")
        print("-" * 96)
        for task_id, metric in results.items():
            b = metric["baseline"]
            a = metric["asg"]
            b_str = f"{'PASS' if b['passed'] else 'FAIL'} / {b['latency_sec']}s / ${b['estimated_cost_usd']:.5f}"
            a_str = f"{'PASS' if a['passed'] else 'FAIL'} / {a['latency_sec']}s / ${a['estimated_cost_usd']:.5f}"
            print(f"{metric['name']:<30} | {b_str:<30} | {a_str:<30}")

if __name__ == "__main__":
    main()


