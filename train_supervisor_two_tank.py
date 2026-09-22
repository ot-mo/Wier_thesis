"""Layer 3 for the two-tank MIMO testbed: offline DeepSeek-driven heuristic
learner for the two-tank supervisor.

Run manually: python train_supervisor_two_tank.py [num_trials]

Mirrors train_supervisor.py's structure (elitist hill-climb, security-check
gate, fixed scenario battery) adapted to the MIMO signature
supervise(telemetry_window, active_setpoints, nominal_targets) and reusing
the same penalty weights already tuned on the single-tank system, since the
underlying reward-shaping lessons (cheap false-positive penalty causes
cry-wolf; no restore-gap term means no pressure to restore) apply here too.
"""

import json
import os
import sys
import time

from openai import OpenAI
from dotenv import load_dotenv

from supervisor_security import check_source, safe_exec_supervisor
from two_tank_sim import MIMO_REQUIRED_ARGS, TwoTankScenarioConfig, run_episode, build_log_report

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

SUPERVISORS_DIR = "generated_supervisors_two_tank"
CURRENT_SUPERVISOR_PATH = os.path.join(SUPERVISORS_DIR, "current_supervisor.py")
RESULTS_DIR = "results"
FAILURE_POINTS_PATH = os.path.join(RESULTS_DIR, "failure_points_two_tank.jsonl")
TRIALS_PATH = os.path.join(RESULTS_DIR, "supervisor_training_trials_two_tank.jsonl")

VIOLATION_PENALTY = 500
MISSED_ANOMALY_PENALTY = 300
FALSE_POSITIVE_PENALTY = 160
NO_FAULT_FALSE_POSITIVE_PENALTY = 800  # per-tank, in scenarios where that tank never faults - no excuse to trigger
EXCEPTION_PENALTY = 10000
RESTORE_GAP_PENALTY = 200

# Guards against a candidate "winning" the averaged score by regressing badly on
# one scenario while gaining on others (composite-score gaming) - a promotion
# is rejected if any individual scenario got worse than this tolerance versus
# the current champion's own score on that scenario, even if the total improved.
REGRESSION_ABS_TOLERANCE = 50.0
REGRESSION_REL_TOLERANCE = 0.05

SCENARIO_BATTERY = [
    TwoTankScenarioConfig(name="baseline_no_fault"),
    TwoTankScenarioConfig(name="tank1_fault_only", leak1_onset_s=5.0, leak1_magnitude=3.0, leak1_offset_s=16.0),
    TwoTankScenarioConfig(name="tank2_fault_only", leak2_onset_s=5.0, leak2_magnitude=3.0, leak2_offset_s=16.0),
    TwoTankScenarioConfig(name="both_faults", leak1_onset_s=5.0, leak1_magnitude=2.0, leak1_offset_s=18.0,
                           leak2_onset_s=8.0, leak2_magnitude=2.0, leak2_offset_s=20.0),
    TwoTankScenarioConfig(name="tank1_severe_persistent", leak1_onset_s=18.0, leak1_magnitude=4.0, leak1_offset_s=None),
    TwoTankScenarioConfig(name="tank2_severe_persistent", leak2_onset_s=18.0, leak2_magnitude=4.0, leak2_offset_s=None),
    TwoTankScenarioConfig(name="noisy_sensor_no_fault", sensor_noise_std=0.1, seed=42),
    TwoTankScenarioConfig(name="noisy_sensor_tank1_fault", leak1_onset_s=5.0, leak1_magnitude=3.0, leak1_offset_s=16.0,
                           sensor_noise_std=0.1, seed=42),
]


def score_supervisor(supervisor_fn):
    """Returns (avg_score, per_scenario_traces)."""
    traces = []
    total = 0.0
    for scenario in SCENARIO_BATTERY:
        result = run_episode(supervisor_fn, scenario)
        m = result["metrics"]
        tank1_never_faults = scenario.leak1_onset_s is None
        tank2_never_faults = scenario.leak2_onset_s is None
        tank1_fp = sum(1 for f in result["failure_points"] if f["type"] == "false_positive" and f["tank"] == "tank1")
        tank2_fp = sum(1 for f in result["failure_points"] if f["type"] == "false_positive" and f["tank"] == "tank2")
        fp_cost = (
            tank1_fp * (NO_FAULT_FALSE_POSITIVE_PENALTY if tank1_never_faults else FALSE_POSITIVE_PENALTY)
            + tank2_fp * (NO_FAULT_FALSE_POSITIVE_PENALTY if tank2_never_faults else FALSE_POSITIVE_PENALTY)
        )
        scenario_score = (
            m["iae"]
            + m["violation_count"] * VIOLATION_PENALTY
            + m["missed_anomaly_count"] * MISSED_ANOMALY_PENALTY
            + fp_cost
            + m["exception_count"] * EXCEPTION_PENALTY
            + m["restore_gap"] * RESTORE_GAP_PENALTY
        )
        total += scenario_score
        traces.append({"scenario": scenario.name, "score": round(scenario_score, 3), "log_report": build_log_report(result)})
    return total / len(SCENARIO_BATTERY), traces


def load_failure_catalog(max_examples_per_category=3):
    if not os.path.exists(FAILURE_POINTS_PATH):
        return {"counts": {}, "examples": {}}

    counts = {}
    examples = {}
    with open(FAILURE_POINTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            key = f"{entry.get('tank', '?')}:{entry.get('type', 'unknown')}"
            counts[key] = counts.get(key, 0) + 1
            examples.setdefault(key, [])
            if len(examples[key]) < max_examples_per_category:
                examples[key].append(entry)
    return {"counts": counts, "examples": examples}


def build_prompt(current_code, best_score, traces, failure_catalog):
    scenario_names = ", ".join(s.name for s in SCENARIO_BATTERY)
    return f"""
You are improving a deterministic supervisory control function for a two-tank
cascade water system. Tank 1 drains via gravity into Tank 2 (Tank 1's outflow
becomes part of Tank 2's inflow), so a fault in either tank can affect the
other - a tank1 leak raises/lowers tank1's outflow into tank2, perturbing
tank2 even when tank2 has no fault of its own. Each tank has its own PID
controller and pump actuator; your job is to coordinate both setpoints, not
just tune each tank's threshold in isolation.

The function signature MUST remain exactly:
def supervise(telemetry_window, active_setpoints, nominal_targets):
    ...
    return {{
        "diagnosis": str,
        "adjusted_setpoints": {{"tank1": float, "tank2": float}},
        "anomaly_flags": {{"tank1": bool, "tank2": bool}},
    }}

telemetry_window is a list of dicts shaped like:
{{"time": float, "tank1": {{"level": float, "pump_effort": float, "error": float}}, "tank2": {{...same keys...}}}}
active_setpoints and nominal_targets are dicts with keys "tank1" and "tank2".

Rules for the code you write:
- No imports, no exec/eval, no file/network/os access, no access to dunder attributes.
- Only use: arithmetic, comparisons, built-in functions (abs, min, max, len, round, sum, sorted, range, all, any, isinstance, etc.), and the `math`/`statistics` modules (already available, do not import them).
- The function must be a pure function of its three arguments plus module-level constants; it will be re-loaded fresh each run, so no persistent state across calls.

Current supervisor source:
```python
{current_code}
```

Current average score across the fixed scenario battery ({scenario_names}): {best_score}
(Lower score is better. Score = IAE(both tanks combined) + violation_count*{VIOLATION_PENALTY} + missed_anomaly_count*{MISSED_ANOMALY_PENALTY} + false_positive_count*FP_PENALTY + exception_count*{EXCEPTION_PENALTY} + restore_gap*{RESTORE_GAP_PENALTY})
(missed_anomaly/false_positive/restore_gap are evaluated PER TANK and summed. restore_gap is |final_setpoint - nominal_target| per tank, counted only when that tank's own fault has fully cleared by episode end.)
(FP_PENALTY is {NO_FAULT_FALSE_POSITIVE_PENALTY} for a tank in a scenario where THAT TANK never faults at all (e.g. tank2 in tank1_fault_only) - there is zero excuse to ever flag it there - and {FALSE_POSITIVE_PENALTY} otherwise.)
(IMPORTANT: a candidate is only promoted if it improves the average score AND does not regress any individual scenario's own score by more than ~5% (or 50 points, whichever is larger) versus the current champion. Do not trade a regression on one scenario - especially a no-fault scenario - for gains on another; such a candidate will be rejected even if the average improves.)

Per-scenario results with the current supervisor (failure_point_counts keys are "tank_name:failure_type"):
{json.dumps(traces, indent=2)}

Aggregate failure-point catalog from past runs (counts and worst examples, keyed "tank_name:failure_type"):
{json.dumps(failure_catalog, indent=2)}

Task:
1. Diagnose what is causing the worst-scoring scenarios and/or the most common failure-point categories, paying attention to whether tank1 and tank2 need different thresholds (they have different physical parameters) and whether a tank1 fault should influence tank2's decision (cascade coupling).
2. Propose an improved `supervise` function that reduces missed anomalies and false positives on both tanks without introducing new safety violations, and that restores both setpoints back toward nominal once their respective faults have genuinely cleared.

You must output strictly JSON matching this structure:
{{
  "failure_analysis": {{
    "what_failed": "string",
    "failing_scenarios": ["scenario_name", ...],
    "change_type": "structural | scalar/config | bug_fix",
    "next_recommendation": "string"
  }},
  "proposed_change": "string",
  "code": "full source of the new supervise function as a string"
}}
"""


def call_deepseek(prompt, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            time.sleep(0.3)
            response = client.chat.completions.create(
                model="deepseek-flash",
                messages=[
                    {"role": "system", "content": "You are a control-systems engineer. Respond ONLY with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"[DEEPSEEK API ERROR - Attempt {attempt}/{max_retries}]: {e}")
            time.sleep(1.0 * attempt)
    print(f"[CRITICAL] All {max_retries} retries failed for this trial.")
    return None


def promote(candidate_code, trial_idx):
    gen_path = os.path.join(SUPERVISORS_DIR, f"supervisor_gen_{trial_idx}.py")
    best_path = os.path.join(SUPERVISORS_DIR, f"best_supervisor_gen_{trial_idx}.py")
    with open(gen_path, "w", encoding="utf-8") as f:
        f.write(candidate_code)
    with open(best_path, "w", encoding="utf-8") as f:
        f.write(candidate_code)
    with open(CURRENT_SUPERVISOR_PATH, "w", encoding="utf-8") as f:
        f.write(candidate_code)
    print(f"[PROMOTED] Trial {trial_idx} is the new current_supervisor.py")


def save_candidate_only(candidate_code, trial_idx):
    gen_path = os.path.join(SUPERVISORS_DIR, f"supervisor_gen_{trial_idx}.py")
    with open(gen_path, "w", encoding="utf-8") as f:
        f.write(candidate_code)


def log_trial(trial_idx, decision, score, failure_analysis, proposed_change, reason=None):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(TRIALS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "trial": trial_idx,
            "decision": decision,
            "score": score,
            "failure_analysis": failure_analysis,
            "proposed_change": proposed_change,
            "reason": reason,
        }) + "\n")


def append_failure_points(traces, run_label):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(FAILURE_POINTS_PATH, "a", encoding="utf-8") as f:
        for trace in traces:
            for key, count in trace["log_report"]["failure_point_counts"].items():
                tank, fp_type = key.split(":", 1)
                f.write(json.dumps({
                    "run": run_label,
                    "scenario": trace["scenario"],
                    "tank": tank,
                    "type": fp_type,
                    "detail": f"count={count} in this evaluation",
                }) + "\n")


def find_scenario_regression(best_traces, cand_traces):
    """Returns (scenario_name, old_score, new_score) for the first scenario where
    the candidate is worse than the champion beyond tolerance, or None if none.
    Guards against composite-score gaming: a lower aggregate score can otherwise
    hide a severe regression on one scenario offset by gains on others.
    """
    for b, c in zip(best_traces, cand_traces):
        allowed = max(REGRESSION_ABS_TOLERANCE, REGRESSION_REL_TOLERANCE * b["score"])
        if c["score"] > b["score"] + allowed:
            return b["scenario"], b["score"], c["score"]
    return None


def _next_trial_start():
    """Continues numbering from the highest existing supervisor_gen_N.py so a
    fresh invocation doesn't silently overwrite a previous run's audit trail.
    """
    existing = [f for f in os.listdir(SUPERVISORS_DIR) if f.startswith("supervisor_gen_") and f.endswith(".py")]
    max_n = 0
    for f in existing:
        try:
            n = int(f[len("supervisor_gen_"):-len(".py")])
            max_n = max(max_n, n)
        except ValueError:
            continue
    return max_n + 1


def main():
    num_trials = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    start_trial = _next_trial_start()

    with open(CURRENT_SUPERVISOR_PATH, "r", encoding="utf-8") as f:
        current_code = f.read()

    ok, reason = check_source(current_code, required_args=MIMO_REQUIRED_ARGS)
    if not ok:
        print(f"[CRITICAL] current_supervisor.py fails its own security check: {reason}")
        sys.exit(1)
    current_fn, err = safe_exec_supervisor(current_code)
    if err:
        print(f"[CRITICAL] current_supervisor.py failed to load: {err}")
        sys.exit(1)

    best_score, best_traces = score_supervisor(current_fn)
    append_failure_points(best_traces, "trainer_baseline")
    print(f"[BASELINE] current_supervisor.py avg score: {best_score:.3f}")

    for i in range(num_trials):
        trial_idx = start_trial + i
        print(f"\n=== Trial {i + 1}/{num_trials} (gen_{trial_idx}) ===")
        failure_catalog = load_failure_catalog()
        prompt = build_prompt(current_code, best_score, best_traces, failure_catalog)
        response = call_deepseek(prompt)

        if response is None:
            log_trial(trial_idx, "SKIPPED", None, None, None, reason="DeepSeek call failed")
            continue

        candidate_code = response.get("code", "")
        failure_analysis = response.get("failure_analysis")
        proposed_change = response.get("proposed_change")

        ok, reason = check_source(candidate_code, required_args=MIMO_REQUIRED_ARGS)
        if not ok:
            print(f"[REJECTED - SECURITY] {reason}")
            log_trial(trial_idx, "REJECTED_SECURITY", None, failure_analysis, proposed_change, reason=reason)
            continue

        candidate_fn, err = safe_exec_supervisor(candidate_code)
        if err:
            print(f"[REJECTED - LOAD] {err}")
            log_trial(trial_idx, "REJECTED_LOAD", None, failure_analysis, proposed_change, reason=err)
            continue

        cand_score, cand_traces = score_supervisor(candidate_fn)
        append_failure_points(cand_traces, f"trainer_trial_{trial_idx}")
        print(f"Candidate score: {cand_score:.3f} (current best: {best_score:.3f})")

        if cand_score < best_score:
            regression = find_scenario_regression(best_traces, cand_traces)
            if regression:
                scen_name, old_s, new_s = regression
                print(f"[REJECTED - REGRESSION] '{scen_name}' regressed {old_s:.1f} -> {new_s:.1f} despite a better aggregate score")
                save_candidate_only(candidate_code, trial_idx)
                log_trial(trial_idx, "REJECTED_REGRESSION", cand_score, failure_analysis, proposed_change,
                          reason=f"{scen_name} regressed {old_s:.1f} -> {new_s:.1f}")
                continue
            promote(candidate_code, trial_idx)
            best_score, current_code, best_traces = cand_score, candidate_code, cand_traces
            log_trial(trial_idx, "PROMOTED", cand_score, failure_analysis, proposed_change)
        else:
            save_candidate_only(candidate_code, trial_idx)
            log_trial(trial_idx, "ROLLBACK", cand_score, failure_analysis, proposed_change)

    print(f"\n[DONE] Final best score: {best_score:.3f}. current_supervisor.py reflects the best candidate found.")


if __name__ == "__main__":
    main()
