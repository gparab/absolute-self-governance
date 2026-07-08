import argparse
import sys
import json
from self_governance.nudger import ContinuousNudger, write_swarm_config_to_stream
from self_governance.dimensioning import dimension_swarm

def main():
    parser = argparse.ArgumentParser(description="Absolute Self-Governance CLI")
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

    args = parser.parse_args()

    if args.subcommand == "run-nudger":
        nudger = ContinuousNudger(working_directory=args.workdir)
        nudger.watch_handoff()
    elif args.subcommand == "trigger-succession":
        with open(args.handoff, "r", encoding="utf-8") as f:
            content = f.read()
        nudger = ContinuousNudger(working_directory=args.workdir)
        nudger.trigger_succession(content)
    elif args.subcommand == "dimension":
        req = json.loads(args.requirements)
        mat = json.loads(args.matrix)
        config = dimension_swarm(req, mat)
        write_swarm_config_to_stream(sys.stdout, config)
        sys.stdout.write("\n")

if __name__ == "__main__":
    main()
