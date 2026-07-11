"""Dashboard visualization module.

Exposes a CLI dashboard displaying key historical metrics of the
Self-Governing Software Factory execution runs.
"""

import sys
from self_governance.learning import get_learning_state


def display_dashboard():
    """Print the historical metrics dashboard of the Self-Governing Software Factory.

    Retrieves aggregated learning state statistics and formats them for CLI display.
    """
    state = get_learning_state()

    sys.stdout.write("==================================================\n")
    sys.stdout.write("      Self-Governing Software Factory Dashboard\n")
    sys.stdout.write("==================================================\n")
    sys.stdout.write(f"Runs Completed:       {state['runs_completed']}\n")
    sys.stdout.write(f"Swarm Success Rate:   {state['success_rate'] * 100:.1f}%\n")
    sys.stdout.write(f"Average Cycle Time:   {state['average_cycle_time']:.2f} s\n")
    sys.stdout.write(f"Vulnerability Alerts: {state['vulnerability_counts']}\n")
    sys.stdout.write(
        f"Matrix Tuning Scale:  {state['matrix_tuning']['scale_factor']:.2f}\n"
    )
    sys.stdout.write("==================================================\n")

