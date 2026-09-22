"""Live entrypoint for the leaky-tank 3-layer supervisory control demo.

Layer 1 (PID) + Layer 2 (deterministic supervisor) run here every cycle via
tank_sim.run_episode. Layer 3 (the offline LLM heuristic learner) lives in
train_supervisor.py and is run manually - it never runs from this script.
"""

import json
import os
import sys
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

from supervisor_security import load_supervisor
from tank_sim import ScenarioConfig, run_episode

CURRENT_SUPERVISOR_PATH = os.path.join("generated_supervisors_leaky_tank", "current_supervisor.py")
RESULTS_DIR = "results"


def main():
    scenario = ScenarioConfig(
        name="live_run",
        sim_time=30.0,
        dt=0.1,
        nominal_setpoint=3.0,
        leak_onset_s=5.0,
        leak_magnitude=3.0,
        leak_offset_s=16.0,
        macro_cycle_steps=30,  # supervisor runs every 30 * 0.1s = 3.0s
    )

    supervisor_fn, err = load_supervisor(CURRENT_SUPERVISOR_PATH)
    if err is not None:
        print(f"[CRITICAL] Could not load Layer-2 supervisor from {CURRENT_SUPERVISOR_PATH}: {err}")
        sys.exit(1)

    print(f"Starting Toy Simulation... (supervisor hash: {supervisor_fn.source_hash[:12]})")
    result = run_episode(supervisor_fn, scenario)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    _write_jsonl_log(result, supervisor_fn.source_hash, timestamp)
    _write_decision_outputs(result)
    _append_failure_points(result, timestamp)
    _plot(result, scenario)


def _write_jsonl_log(result, source_hash, timestamp):
    path = os.path.join(RESULTS_DIR, f"live_run_{timestamp}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event": "run_start", "supervisor_source_hash": source_hash}) + "\n")
        for i, t in enumerate(result["time_hist"]):
            f.write(json.dumps({
                "event": "step",
                "time": t,
                "level": result["level_hist"][i],
                "pump_effort": result["pump_hist"][i],
                "setpoint": result["setpoint_hist"][i],
                "leak": result["leak_hist"][i],
            }) + "\n")
        for decision in result["supervisor_decisions"]:
            f.write(json.dumps({"event": "supervisor_decision", **decision}) + "\n")
        for fp in result["failure_points"]:
            f.write(json.dumps({"event": "failure_point", **fp}) + "\n")
    print(f"[SUCCESS] Structured run log saved to: {path}")


def _write_decision_outputs(result):
    json_path = os.path.join(RESULTS_DIR, "supervisor_decisions.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result["supervisor_decisions"], f, indent=2, ensure_ascii=False)
    print(f"[SUCCESS] Supervisor decisions JSON saved to: {json_path}")

    csv_path = os.path.join(RESULTS_DIR, "supervisor_decisions.csv")
    pd.DataFrame(result["supervisor_decisions"]).to_csv(csv_path, index=False)
    print(f"[SUCCESS] Supervisor decisions CSV saved to: {csv_path}")


def _append_failure_points(result, timestamp):
    path = os.path.join(RESULTS_DIR, "failure_points.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for fp in result["failure_points"]:
            f.write(json.dumps({"run": f"live_{timestamp}", "scenario": result["scenario"], **fp}) + "\n")


def _plot(result, scenario):
    time_hist = result["time_hist"]
    level_hist = result["level_hist"]
    setpoint_hist = result["setpoint_hist"]
    pump_hist = result["pump_hist"]
    leak_hist = result["leak_hist"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

    ax1.plot(time_hist, level_hist, label="Water Level $h(t)$", color="blue", linewidth=2)
    ax1.plot(time_hist, setpoint_hist, label="Active Setpoint", color="red", linestyle="--", linewidth=1.8)
    ax1.axhline(scenario.nominal_setpoint, label=f"Nominal Target ({scenario.nominal_setpoint} m)", color="gray", linestyle=":", alpha=0.7)
    ax1.fill_between(time_hist, 0, max(setpoint_hist) + 0.5, where=(np.array(leak_hist) > 0),
                      color="orange", alpha=0.15, label="Leak Disturbance Active")
    ax1.set_ylabel("Water Level (m)")
    ax1.set_title("Deterministic Supervisory Control: Response to Leak & Recovery")
    ax1.legend(loc="upper right")
    ax1.grid(True, linestyle="--", alpha=0.6)

    ax2.plot(time_hist, pump_hist, label="Pump Effort $u(t)$", color="green", linewidth=1.5)
    ax2.plot(time_hist, leak_hist, label="Leak Rate $d(t)$", color="darkred", linestyle="-.", linewidth=1.5)
    ax2.set_xlabel("Time (seconds)")
    ax2.set_ylabel("Control Effort / Flow Rate")
    ax2.legend(loc="upper right")
    ax2.grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    save_path = os.path.join(RESULTS_DIR, "supervisory_response.png")
    plt.savefig(save_path, dpi=300, bbox_inches="tight")
    print(f"[SUCCESS] Plot saved to: {save_path}")
    plt.show()


if __name__ == "__main__":
    main()
