"""Benchmarks candidate two-tank MIMO supervisors against a PID-only baseline.

Run manually: python benchmark_two_tank.py

Mirrors benchmark_baselines.py's structure for the single-tank system. The
scenario battery covers faults isolated to each tank individually (to see
whether a supervisor recognizes that a tank1 fault should also concern
tank2, given the cascade coupling) and faults in both simultaneously.
"""

from supervisor_security import safe_exec_supervisor
from two_tank_sim import TwoTankScenarioConfig, run_episode, build_log_report

SCENARIO_BATTERY = [
    TwoTankScenarioConfig(name="baseline_no_fault"),
    TwoTankScenarioConfig(name="tank1_fault_only", leak1_onset_s=5.0, leak1_magnitude=3.0, leak1_offset_s=16.0),
    TwoTankScenarioConfig(name="tank2_fault_only", leak2_onset_s=5.0, leak2_magnitude=3.0, leak2_offset_s=16.0),
    TwoTankScenarioConfig(name="both_faults", leak1_onset_s=5.0, leak1_magnitude=2.0, leak1_offset_s=18.0,
                           leak2_onset_s=8.0, leak2_magnitude=2.0, leak2_offset_s=20.0),
    TwoTankScenarioConfig(name="tank1_severe_late", leak1_onset_s=18.0, leak1_magnitude=4.0, leak1_offset_s=None),
]

VIOLATION_PENALTY = 500
MISSED_ANOMALY_PENALTY = 300
FALSE_POSITIVE_PENALTY = 160
EXCEPTION_PENALTY = 10000
RESTORE_GAP_PENALTY = 200


def score_supervisor(supervisor_fn):
    traces = []
    total = 0.0
    for scenario in SCENARIO_BATTERY:
        result = run_episode(supervisor_fn, scenario)
        m = result["metrics"]
        scenario_score = (
            m["iae"]
            + m["violation_count"] * VIOLATION_PENALTY
            + m["missed_anomaly_count"] * MISSED_ANOMALY_PENALTY
            + m["false_positive_count"] * FALSE_POSITIVE_PENALTY
            + m["exception_count"] * EXCEPTION_PENALTY
            + m["restore_gap"] * RESTORE_GAP_PENALTY
        )
        total += scenario_score
        traces.append({"scenario": scenario.name, "score": round(scenario_score, 3), "log_report": build_log_report(result)})
    return total / len(SCENARIO_BATTERY), traces


def pid_only_supervisor(telemetry_window, active_setpoints, nominal_targets):
    return {
        "diagnosis": "PID-only baseline: no supervisory action.",
        "adjusted_setpoints": dict(nominal_targets),
        "anomaly_flags": {"tank1": False, "tank2": False},
    }


def print_report(label, avg_score, traces):
    print(f"\n=== {label}: avg score {avg_score:.2f} ===")
    for t in traces:
        m = t["log_report"]["metrics"]
        print(
            f"  {t['scenario']:20s} score={t['score']:>10.2f}  "
            f"iae={m['iae']:>7.2f}  violations={m['violation_count']}  "
            f"missed={m['missed_anomaly_count']:>3}  false_pos={m['false_positive_count']:>3}  "
            f"restore_gap={m['restore_gap']:.2f}"
        )


def main():
    candidates = [("PID-only (no supervisor)", pid_only_supervisor)]

    with open("generated_supervisors_two_tank/current_supervisor.py", "r", encoding="utf-8") as f:
        code = f.read()
    fn, err = safe_exec_supervisor(code)
    if err:
        raise RuntimeError(f"current_supervisor.py failed to load: {err}")
    candidates.append(("Current two-tank supervisor", fn))

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
