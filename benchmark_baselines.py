"""Benchmarks the current trained supervisor against simple baselines.

Run manually: python benchmark_baselines.py

Currently compares against a PID-only baseline (no supervisory layer at all -
the setpoint is held at nominal_target regardless of telemetry). This is a
placeholder for the eventual real benchmark: once real plant data and the
company's actual controller are available, this script's structure (run each
candidate through the same scenario battery via score_supervisor) is the
pattern to extend for a proper RL/TD-MPC comparison.
"""

import os

from supervisor_security import safe_exec_supervisor
from train_supervisor import SCENARIO_BATTERY, score_supervisor

SUPERVISORS_DIR = "generated_supervisors_leaky_tank"


def pid_only_supervisor(telemetry_window, active_setpoint, nominal_target):
    """No supervisory layer at all: setpoint is always held at nominal_target,
    anomaly detection is never performed. This isolates the low-level PID's own
    performance from any contribution the supervisory layer makes.
    """
    return {
        "diagnosis": "PID-only baseline: no supervisory action.",
        "adjusted_setpoint": nominal_target,
        "anomaly_flag": False,
    }


def load_gen(trial_idx):
    path = os.path.join(SUPERVISORS_DIR, f"supervisor_gen_{trial_idx}.py")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()
    fn, err = safe_exec_supervisor(code)
    if err:
        raise RuntimeError(f"{path} failed to load: {err}")
    return fn


def print_report(label, avg_score, traces):
    print(f"\n=== {label}: avg score {avg_score:.2f} ===")
    for t in traces:
        m = t["log_report"]["metrics"]
        print(
            f"  {t['scenario']:26s} score={t['score']:>10.2f}  "
            f"iae={m['iae']:>7.2f}  violations={m['violation_count']}  "
            f"missed={m['missed_anomaly_count']:>3}  false_pos={m['false_positive_count']:>3}  "
            f"restore_gap={m['restore_gap']:.2f}"
        )


def main():
    candidates = [("PID-only (no supervisor)", pid_only_supervisor)]

    if os.path.exists(os.path.join(SUPERVISORS_DIR, "supervisor_gen_0.py")):
        candidates.append(("Seed heuristic (gen_0)", load_gen(0)))

    current_path = os.path.join(SUPERVISORS_DIR, "current_supervisor.py")
    with open(current_path, "r", encoding="utf-8") as f:
        current_code = f.read()
    current_fn, err = safe_exec_supervisor(current_code)
    if err:
        raise RuntimeError(f"current_supervisor.py failed to load: {err}")
    candidates.append(("Current trained supervisor", current_fn))

    scenario_names = ", ".join(s.name for s in SCENARIO_BATTERY)
    print(f"Scenario battery: {scenario_names}")

    results = []
    for label, fn in candidates:
        avg_score, traces = score_supervisor(fn)
        print_report(label, avg_score, traces)
        results.append((label, avg_score))

    print("\n=== Summary (lower is better) ===")
    for label, avg_score in results:
        print(f"  {label:30s} {avg_score:>10.2f}")


if __name__ == "__main__":
    main()
