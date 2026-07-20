"""Command Line Interface (CLI) module for Absolute Self-Governance.

Provides various subcommands to run the continuous nudger, trigger succession,
compute swarm dimensions, show metrics, serve development mode, and save/restore sessions.
"""

import argparse
import os
import sys
import json
from datetime import datetime, timezone

from self_governance.db import SessionLocal, TokenUsage, Milestone, AgentMemory
from self_governance.nudger import ContinuousNudger, write_swarm_config_to_stream
from self_governance.dimensioning import dimension_swarm
from self_governance.config import OrchestratorConfig
from self_governance.dashboard import display_dashboard


def parse_args():
    """Parses and validates command-line arguments.

    Returns:
        argparse.Namespace: The parsed arguments namespace.
    """
    try:
        import importlib.metadata
        version = importlib.metadata.version("absolute-self-governance")
    except importlib.metadata.PackageNotFoundError:
        version = "0.1.3"

    parser = argparse.ArgumentParser(description="Absolute Self-Governance CLI")
    parser.add_argument(
        "--version",
        action="version",
        version=f"absolute-self-governance {version}",
    )
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

    # demo subcommand: zero-setup, zero-cost walkthrough of dynamic team sizing
    parser_demo = subparsers.add_parser(
        "demo", help="Zero-setup demo of dynamic swarm sizing (no API key needed)"
    )
    parser_demo.add_argument(
        "--pause", type=float, default=3.0,
        help="Seconds to pause between scenarios (default: 3.0).",
    )

    # mcp-server subcommand: exposes dimension_swarm as an MCP tool over
    # stdio, so any MCP client (Claude Desktop, Claude Code, Cursor, etc.)
    # can call ASG's swarm-sizing math directly.
    subparsers.add_parser(
        "mcp-server",
        help="Run ASG's dynamic swarm-sizing tool as an MCP server over stdio",
    )

    # onboard subcommand: guided tenant + webhook setup, replacing manual
    # config-file editing and clicking through GitHub Settings by hand
    parser_onboard = subparsers.add_parser(
        "onboard", help="Guided setup: provision a tenant and wire the GitHub webhook"
    )
    parser_onboard.add_argument(
        "--name", required=True, help="A name for this tenant (e.g. your org or repo name)."
    )
    parser_onboard.add_argument(
        "--repo", default=None,
        help="GitHub 'owner/repo' to wire the webhook to. Requires --github-token "
             "and --base-url to auto-register; otherwise prints manual steps.",
    )
    parser_onboard.add_argument(
        "--github-token", default=None,
        help="A GitHub personal access token with repo admin:repo_hook scope, "
             "used only to register the webhook -- never stored.",
    )
    parser_onboard.add_argument(
        "--base-url", default=None,
        help="Publicly reachable base URL where this instance's /webhook "
             "endpoint can receive GitHub's requests (e.g. an ngrok/tunnel "
             "URL or your deployed host).",
    )



    # session-save subcommand
    parser_save = subparsers.add_parser(
        "session-save", aliases=["session_save", "save-session", "save_session"],
        help="Serialize Nudger session state to a JSON file"
    )
    parser_save.add_argument(
        "--file", default="asg_session.json", help="Output JSON session file (default: 'asg_session.json')"
    )
    parser_save.add_argument(
        "--workdir", default=".", help="Working directory (default: '.')"
    )

    # session-restore subcommand
    parser_restore = subparsers.add_parser(
        "session-restore", aliases=["session_restore", "restore-session", "restore_session"],
        help="Restore Nudger session state from a JSON file"
    )
    parser_restore.add_argument(
        "--file", default="asg_session.json", help="Input JSON session file (default: 'asg_session.json')"
    )
    parser_restore.add_argument(
        "--workdir", default=".", help="Working directory (default: '.')"
    )

    # security-audit subcommand
    parser_sec = subparsers.add_parser(
        "security-audit", aliases=["security_audit"],
        help="Run OWASP + STRIDE security audit on a file or inline string"
    )
    parser_sec.add_argument(
        "target", nargs="?", default="handoff.md",
        help="File path or inline payload to audit (default: handoff.md)"
    )
    parser_sec.add_argument(
        "--fail-on-high", action="store_true", default=False,
        help="Also fail on HIGH severity findings"
    )
    parser_sec.add_argument(
        "--output-json", action="store_true", default=False,
        help="Output findings as JSON"
    )

    # retro subcommand
    parser_retro = subparsers.add_parser(
        "retro",
        help="Print a retrospective report from the learning distillation log"
    )
    parser_retro.add_argument(
        "--export", metavar="FILE", default=None,
        help="Write the retro report as markdown to FILE instead of printing"
    )
    # import-agents subcommand
    parser_import = subparsers.add_parser(
        "import-agents", aliases=["import_agents"],
        help="Import agents from a JSON/YAML file and write to the static registry asset location"
    )
    parser_import.add_argument(
        "file", help="Path to JSON/YAML file containing agent profiles to import"
    )

    # inject-constraint subcommand
    parser_inject = subparsers.add_parser(
        "inject-constraint", help="Inject a live constraint mid-execution for God's Eye thermal escape."
    )
    parser_inject.add_argument(
        "constraint", help="The text constraint to inject (e.g., 'Use Postgres')"
    )
    parser_inject.add_argument(
        "--workdir", default=".", help="Working directory (default: '.')"
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
    parser_bench.add_argument(
        "--out", type=str, default=None,
        help="Path to save or resume JSONL benchmark outcomes.",
    )
    parser_bench.add_argument(
        "--model", type=str, default=None,
        help="Model name to use for every call in the sweep, e.g. "
             "'gemini-2.5-flash' (default: whatever config.yaml/"
             "OrchestratorConfig configures). Baseline and ASG modes "
             "always run against the same model within one sweep.",
    )
    parser_bench.add_argument(
        "--tasks", type=str, default=None,
        help="Comma-separated task ids to restrict the sweep to (default: "
             "all tasks), e.g. --tasks task_lru_cache,task_retry_backoff. "
             "Useful for concentrating reps on tasks that show variance "
             "instead of spending on tasks already at ceiling.",
    )
    parser_bench.add_argument(
        "--task-source", type=str, default=None,
        help="Path to an alternate tasks JSON file (default: the "
             "packaged benchmark_tasks.json), e.g. "
             "src/self_governance/benchmark_tasks_heldout.json to run "
             "the held-out overfitting-control tier.",
    )
    parser_bench.add_argument(
        "--include-recursive-ablation", action="store_true",
        help="Also run a third arm: one persona recursively refining its "
             "own attempt across the same 3-attempt budget, instead of 3 "
             "distinct personas rotating (the default 'asg' arm) -- a "
             "direct width-vs-depth ablation (see run_asg_mode's "
             "persona_strategy docstring). Triples LLM spend for the ASG "
             "arm. Only supported with --reps 1 (the sequential path).",
    )

    return parser.parse_args()


def handle_run_nudger(args, config):
    """Starts the continuous nudger to monitor handoffs and manage succession.

    Args:
        args: Parsed command-line arguments.
        config: The OrchestratorConfig configuration instance.
    """
    import signal

    nudger = ContinuousNudger(working_directory=args.workdir, config=config)
    # k8s/systemd stop sends SIGTERM: exit cleanly instead of mid-succession
    signal.signal(signal.SIGTERM, lambda *_: nudger.stop())
    nudger.watch_handoff()


def handle_trigger_succession(args, config):
    """Manually triggers succession based on a specific handoff file.

    Args:
        args: Parsed command-line arguments.
        config: The OrchestratorConfig configuration instance.
    """
    with open(args.handoff, "r", encoding="utf-8") as f:
        content = f.read()
    nudger = ContinuousNudger(working_directory=args.workdir, config=config)
    nudger.trigger_succession(content)


def handle_dimension(args):
    """Computes and writes the optimal swarm configuration based on requirement complexity.

    Args:
        args: Parsed command-line arguments.
    """
    req = json.loads(args.requirements)
    mat = json.loads(args.matrix)
    swarm_config = dimension_swarm(req, mat)
    write_swarm_config_to_stream(sys.stdout, swarm_config)
    sys.stdout.write("\n")


def handle_inject_constraint(args):
    """Injects a live constraint into the nudger's interrupt.md file.

    Args:
        args: Parsed command-line arguments.
    """
    interrupt_path = os.path.join(args.workdir, "interrupt.md")
    with open(interrupt_path, "w", encoding="utf-8") as f:
        f.write(args.constraint)
    print(f"Successfully injected constraint into {interrupt_path}")


def handle_stats(args):
    """Displays the metrics dashboard.

    Args:
        args: Parsed command-line arguments.
    """
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


def handle_dev(args, config):
    """Runs a dev monitor server and the continuous nudger in parallel.

    Args:
        args: Parsed command-line arguments.
        config: The OrchestratorConfig configuration instance.
    """
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



def handle_demo(args) -> None:
    """Runs the zero-setup, zero-cost demo of dynamic swarm sizing.

    Args:
        args: Parsed command-line arguments.
    """
    import logging
    from self_governance.demo import run_demo

    # The demo's whole point is a clean, narrated 30-second read -- every
    # other subcommand wants its INFO-level consensus/nudger logs, but here
    # they'd bury the narrated output under ~80 lines of raw log noise
    # before a first-time visitor ever sees the actual point.
    logging.getLogger().setLevel(logging.WARNING)

    print("ASG demo: no API key required, zero cost, zero setup.\n")
    run_demo(pause_seconds=args.pause)


def handle_mcp_server(args) -> None:
    """Runs ASG's dynamic swarm-sizing tool as an MCP server over stdio.

    Args:
        args: Parsed command-line arguments (unused; no options today).
    """
    from self_governance.mcp_server import main as run_mcp_server

    run_mcp_server()


def handle_onboard(args) -> None:
    """Guided setup: provisions a tenant and, if given a repo, GitHub token,
    and base URL, auto-registers the webhook; otherwise prints the manual
    steps to do it by hand.

    Args:
        args: Parsed command-line arguments.
    """
    from self_governance.onboarding import run_onboarding

    result = run_onboarding(
        tenant_name=args.name,
        repo=args.repo,
        github_token=args.github_token,
        base_url=args.base_url,
    )

    print("\n" + "=" * 60)
    print("  ASG onboarding")
    print("=" * 60)
    print(f"  Tenant ID:      {result['tenant_id']}")
    print(f"  API key:        {result['api_key']}")
    print("                  (store this now -- it will not be shown again)")
    print(f"  Webhook secret: {result['webhook_secret']}")

    if result["webhook_auto_registered"]:
        print(f"\n  GitHub webhook registered automatically at {result['webhook_url']}")
        print(f"  (webhook id: {result.get('webhook_id')})")
    else:
        if result.get("webhook_registration_error"):
            print(f"\n  Automatic webhook registration failed: {result['webhook_registration_error']}")
        print("\n  Next steps (manual webhook setup):")
        print("  1. Deploy this instance somewhere GitHub can reach it, or")
        print("     start a tunnel (e.g. `ngrok http 8000`).")
        print("  2. In your repo: Settings -> Webhooks -> Add webhook")
        print("       Payload URL:  <your-base-url>/webhook")
        print("       Content type: application/json")
        print(f"       Secret:       {result['webhook_secret']}")
        print("       Events:       Issues, Pull requests")
        print("  3. Set these environment variables before starting the server:")
        print(f"       export WEBHOOK_SECRET={result['webhook_secret']}")
        print("       export GEMINI_API_KEY=...")
        print("  4. Verify it's wired: curl <your-base-url>/status "
              f"-H 'Authorization: Bearer {result['api_key']}'")
    print("=" * 60)


def handle_retro(args) -> None:
    """Prints a retrospective report from the learning distillation log.

    Shows patterns, anti-patterns, roster evolution, and recommendations
    derived from all recorded succession sessions.

    Args:
        args: Parsed command-line arguments (args.export, args.as_json).
    """
    from self_governance.learning import get_learning_state, format_retro_report

    state = get_learning_state()

    if args.as_json:
        print(json.dumps(state, indent=2, default=str))
        return

    report = format_retro_report(state)

    if args.export:
        with open(args.export, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Retro report written to {args.export}")
    else:
        print(report)


def handle_session_save(args, config):
    """Serializes the active Nudger session state to a JSON file.

    Saves wallet cost, active topologies, pending milestones, and metadata.

    Args:
        args: Parsed command-line arguments.
        config: The OrchestratorConfig configuration instance.
    """
    db = SessionLocal()
    try:
        # Calculate wallet spent
        try:
            token_usages = db.query(TokenUsage).all()
            spent = sum(u.cost_usd for u in token_usages)
        except Exception:
            spent = 0.0

        # Fetch milestones
        milestone_list = []
        try:
            milestones = db.query(Milestone).all()
            for m in milestones:
                milestone_list.append({
                    "id": m.id,
                    "name": m.name,
                    "status": m.status,
                    "dependencies": m.dependencies
                })
        except Exception as e:
            print(f"Failed to fetch milestones: {e}")

        # Fetch active topologies & memories
        memory_list = []
        active_topologies = []
        try:
            memories = db.query(AgentMemory).all()
            for m in memories:
                memory_list.append({
                    "key": m.key,
                    "agent_id": m.agent_id,
                    "value": m.value
                })
                if "topology" in m.key:
                    active_topologies.append({
                        "key": m.key,
                        "agent_id": m.agent_id,
                        "value": m.value
                    })
        except Exception as e:
            print(f"Failed to fetch memories: {e}")

        # Save to file
        session_data = {
            "wallet": {
                "spent": spent,
                "max_budget": 0.50
            },
            "active_topologies": active_topologies,
            "pending_milestones": milestone_list,
            "cached_metadata": {
                "memories": memory_list,
                "saved_at": datetime.now(timezone.utc).isoformat()
            }
        }

        file_path = args.file
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)
        print(f"Session saved successfully to {file_path}")
    finally:
        db.close()


def handle_session_restore(args, config):
    """Restores Nudger session state from a serialized JSON file.

    Overwrites existing milestones, token usage, and agent memories in the database.

    Args:
        args: Parsed command-line arguments.
        config: The OrchestratorConfig configuration instance.
    """
    file_path = args.file
    if not os.path.exists(file_path):
        print(f"Session file not found: {file_path}")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        session_data = json.load(f)

    db = SessionLocal()
    try:
        # Clear and restore milestones
        try:
            db.query(Milestone).delete()
            for m in session_data.get("pending_milestones", []):
                db.add(Milestone(
                    id=m.get("id"),
                    name=m.get("name"),
                    status=m.get("status"),
                    dependencies=m.get("dependencies")
                ))
        except Exception as e:
            print(f"Failed to restore milestones: {e}")

        # Clear and restore token usage (wallet spent)
        try:
            db.query(TokenUsage).delete()
            wallet_data = session_data.get("wallet", {})
            spent = wallet_data.get("spent", 0.0)
            if spent > 0:
                db.add(TokenUsage(
                    tenant_id="default",
                    cost_usd=spent
                ))
        except Exception as e:
            print(f"Failed to restore token usage: {e}")

        # Clear and restore memories/metadata
        try:
            db.query(AgentMemory).delete()
            cached_metadata = session_data.get("cached_metadata", {})
            for m in cached_metadata.get("memories", []):
                db.add(AgentMemory(
                    key=m.get("key"),
                    agent_id=m.get("agent_id"),
                    value=m.get("value")
                ))
        except Exception as e:
            print(f"Failed to restore memories: {e}")

        db.commit()
        print(f"Session restored successfully from {file_path}")
    except Exception as e:
        db.rollback()
        print(f"Failed to restore session: {e}")
        sys.exit(1)
    finally:
        db.close()


def handle_security_audit(args) -> None:
    """Runs an OWASP + STRIDE security audit on a file or inline string payload.

    Exits with code 1 when the audit fails.

    Args:
        args: Parsed command-line arguments.
    """
    from self_governance.security import run_security_audit

    target = args.target
    if os.path.exists(target):
        with open(target, "r", encoding="utf-8") as f:
            payload = f.read()
        print(f"Auditing file: {target}")
    else:
        payload = target
        print("Auditing inline payload")

    result = run_security_audit(payload, fail_on_critical=True, fail_on_high=args.fail_on_high)

    if args.output_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Security Audit Result: {'✅ PASSED' if result.passed else '❌ FAILED'}")
        print(f"Summary: {result.audit_summary}")
        print(f"{'='*60}")

        if result.findings:
            print("\nFindings:")
            icon_map = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
            for finding in result.findings:
                icon = icon_map.get(finding.severity, "⚪")
                print(f"  {icon} [{finding.severity}] {finding.category}")
                print(f"     Description: {finding.description}")
                print(f"     Pattern:     {finding.pattern_matched!r}")
                print(f"     Remediation: {finding.remediation}")
        else:
            print("\nNo findings — clean audit.")

    if not result.passed:
        sys.exit(1)


def handle_import_agents(args) -> None:
    """Imports agents from a JSON or YAML file, validates them, and writes to static registry asset location."""
    import json
    import yaml
    from pydantic import BaseModel, Field
    from typing import List, Optional

    file_path = args.file
    if not os.path.exists(file_path):
        print(f"Error: Import file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    try:
        ext = os.path.splitext(file_path)[1].lower()
        with open(file_path, "r", encoding="utf-8") as f:
            if ext in (".yaml", ".yml"):
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
    except Exception as e:
        print(f"Error reading or parsing import file: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict) or ("sdlc" not in data and "council" not in data):
        print("Error: Import data must be a dictionary containing 'sdlc' and/or 'council' keys.", file=sys.stderr)
        sys.exit(1)

    class ImportedAgentProfile(BaseModel):
        role: str
        prompt: str
        capabilities: List[str] = Field(default_factory=list)
        developer_message: Optional[str] = None
        division: Optional[str] = None
        emoji: Optional[str] = None
        vibe: Optional[str] = None
        description: Optional[str] = None
        quality_gate: Optional[dict] = None

    validated_sdlc = {}
    validated_council = {}
    errors = 0

    for category in ("sdlc", "council"):
        category_data = data.get(category, {})
        if not isinstance(category_data, dict):
            continue
        for role_name, profile in category_data.items():
            if not isinstance(profile, dict):
                print(f"Error: Profile for '{role_name}' must be a dictionary.", file=sys.stderr)
                errors += 1
                continue
            try:
                if "role" not in profile:
                    profile["role"] = role_name
                ImportedAgentProfile.model_validate(profile)
                if category == "sdlc":
                    validated_sdlc[role_name] = profile
                else:
                    validated_council[role_name] = profile
            except Exception as ve:
                print(f"Validation failed for {category} agent '{role_name}': {ve}", file=sys.stderr)
                errors += 1

    if errors > 0:
        print(f"Import halted. Found {errors} schema validation errors.", file=sys.stderr)
        sys.exit(1)

    this_dir = os.path.dirname(os.path.abspath(__file__))
    assets_file = os.path.join(this_dir, "assets", "agents.json")
    os.makedirs(os.path.dirname(assets_file), exist_ok=True)

    output_data = {
        "sdlc": validated_sdlc,
        "council": validated_council
    }

    try:
        with open(assets_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
        print(f"Successfully imported {len(validated_sdlc)} SDLC agents and {len(validated_council)} Council agents to {assets_file}")
    except Exception as e:
        print(f"Error writing to static registry: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main entry point for the Absolute Self-Governance CLI.

    Parses command-line arguments and routes to the appropriate subcommand handler.
    """
    args = parse_args()
    from self_governance.telemetry import setup_telemetry, new_correlation_id

    setup_telemetry(json_logging=args.json_logs)
    new_correlation_id()

    config = OrchestratorConfig(args.config)

    handlers = {
        "run-nudger": lambda: handle_run_nudger(args, config),
        "trigger-succession": lambda: handle_trigger_succession(args, config),
        "dimension": lambda: handle_dimension(args),
        "stats": lambda: handle_stats(args),
        "dev": lambda: handle_dev(args, config),
        "demo": lambda: handle_demo(args),
        "mcp-server": lambda: handle_mcp_server(args),
        "onboard": lambda: handle_onboard(args),

        "session-save": lambda: handle_session_save(args, config),
        "session_save": lambda: handle_session_save(args, config),
        "save-session": lambda: handle_session_save(args, config),
        "save_session": lambda: handle_session_save(args, config),
        "session-restore": lambda: handle_session_restore(args, config),
        "session_restore": lambda: handle_session_restore(args, config),
        "restore-session": lambda: handle_session_restore(args, config),
        "restore_session": lambda: handle_session_restore(args, config),
        "security-audit": lambda: handle_security_audit(args),
        "security_audit": lambda: handle_security_audit(args),
        "retro": lambda: handle_retro(args),
        "import-agents": lambda: handle_import_agents(args),
        "import_agents": lambda: handle_import_agents(args),
        "inject-constraint": lambda: handle_inject_constraint(args),
        "benchmark": lambda: handle_benchmark(args),
    }

    handler = handlers.get(args.subcommand)
    if handler:
        handler()
    else:
        print(f"Unknown subcommand: {args.subcommand}", file=sys.stderr)
        sys.exit(1)


def handle_benchmark(args):
    if args.reps <= 1:
        from self_governance.benchmark import run_benchmark

        results = run_benchmark(
            out_path=args.out,
            model=args.model,
            include_recursive_ablation=args.include_recursive_ablation,
        )
        header = f"\n{'Task Name':<30} | {'Baseline (Pass/Time/Cost)':<30} | {'ASG Mode (Pass/Time/Cost)':<30}"
        if args.include_recursive_ablation:
            header += f" | {'ASG Recursive (Pass/Time/Cost)':<32}"
        print(header)
        print("-" * (96 if not args.include_recursive_ablation else 130))
        for task_id, metric in results.items():
            b = metric["baseline"]
            a = metric["asg"]
            b_str = f"{'PASS' if b['passed'] else 'FAIL'} / {b['latency_sec']}s / ${b['estimated_cost_usd']:.5f}"
            a_str = f"{'PASS' if a['passed'] else 'FAIL'} / {a['latency_sec']}s / ${a['estimated_cost_usd']:.5f}"
            row = f"{metric['name']:<30} | {b_str:<30} | {a_str:<30}"
            if args.include_recursive_ablation:
                r = metric["asg_recursive"]
                r_str = f"{'PASS' if r['passed'] else 'FAIL'} / {r['latency_sec']}s / ${r['estimated_cost_usd']:.5f}"
                row += f" | {r_str:<32}"
            print(row)
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

        task_ids = args.tasks.split(",") if args.tasks else None
        print(
            f"Running {args.reps} reps/task/mode with {args.workers} "
            f"concurrent workers (each in its own isolated tempdir)"
            f"{f', model={args.model}' if args.model else ''}"
            f"{f', tasks={task_ids}' if task_ids else ''}...\n"
        )
        results = run_benchmark_parallel(
            reps=args.reps,
            workers=args.workers,
            on_result=_progress,
            resume_path=args.out,
            model=args.model,
            task_ids=task_ids,
            task_source=args.task_source,
        )

        print(f"\n| {'Task':<24} | {'Mode':<9} | {'Pass':<8} | {'MeanLat':<9} | {'MeanCost':<10} |")
        print("|" + "-" * 26 + "|" + "-" * 11 + "|" + "-" * 10 + "|" + "-" * 11 + "|" + "-" * 12 + "|")
        for task_id, data in results.items():
            for mode in ("baseline", "asg"):
                runs = data[mode]
                n = len(runs)
                if n == 0:
                    continue
                passed = sum(1 for r in runs if r.get("passed"))
                mean_lat = sum(r.get("latency_sec", 0) for r in runs) / n
                mean_cost = sum(r.get("estimated_cost_usd", 0) for r in runs) / n
                print(
                    f"| {task_id:<24} | {mode:<9} | {passed}/{n:<6} | "
                    f"{mean_lat:<9.1f} | ${mean_cost:<9.6f} |"
                )


if __name__ == "__main__":
    main()
