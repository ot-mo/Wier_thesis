import csv
import json
import os
import time
import numpy as np
from google import genai
from google.genai import errors
from BallMIll import BallMillSimulator
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("API_KEY")

# Initialize Gemini Client
client = genai.Client(api_key=api_key)
model = "gemini-3.6-flash"
# Directories & Audit Trail setup
RESULTS_DIR = "results"
POLICIES_DIR = "generated_policies"
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(POLICIES_DIR, exist_ok=True)

TRIALS_LOG = os.path.join(RESULTS_DIR, "trials.jsonl")  # Authoritative audit trail
SUMMARY_CSV = os.path.join(RESULTS_DIR, "summary.csv")
FINAL_REPORT_MD = os.path.join(RESULTS_DIR, "final_report.md")

# Fixed development seeds for regression testing
DEV_SEEDS = [42, 101, 2024]


def run_fixed_seed_eval(policy_func, seeds=DEV_SEEDS):
    per_seed_results = []
    total_score = 0.0

    for seed in seeds:
        np.random.seed(seed)
        sim = BallMillSimulator(dt=0.1)
        obs = sim.reset()

        iae_level, iae_p80, energy, violations = 0.0, 0.0, 0.0, 0
        duration_steps = 120

        for step in range(duration_steps):
            base_hardness = 1.25 if step > 60 else 1.0  # Process disturbance[cite: 1]

            hardness_noise = np.random.normal(0, 0.05)   # Ore hardness fluctuates
            feed_noise = np.random.normal(0, 15.0)       # Upstream feed surge (t/h)
            
            ore_hardness = max(0.5, base_hardness + hardness_noise)
            fresh_feed = max(200.0, 320.0 + feed_noise)

            pump_speed, sump_water, mill_water = policy_func(obs)

            obs = sim.step(
                pump_speed,
                sump_water,
                mill_water,
                fresh_feed=fresh_feed,
                ore_hardness=ore_hardness,
            )

            iae_level += abs(obs["sump_level"] - 50.0) * 0.1
            iae_p80 += abs(obs["product_p80"] - 75.0) * 0.1
            energy += obs["mill_power"] * (0.1 / 60.0)

            if obs["sump_level"] > 90.0 or obs["sump_level"] < 15.0:
                violations += 1
            if obs["mill_power"] < 1500.0:
                violations += 1

        seed_score = iae_level + iae_p80 + (violations * 1000.0)
        total_score += seed_score

        per_seed_results.append({
            "seed": seed,
            "iae_level": round(iae_level, 2),
            "iae_p80": round(iae_p80, 2),
            "energy_kwh": round(energy, 2),
            "violations": violations,
            "score": round(seed_score, 2),
        })

    avg_score = round(total_score / len(seeds), 2)
    return avg_score, per_seed_results


def optimize_heuristic_with_ledger(
    previous_code: str,
    prev_metrics: dict,
    trial_num: int,
    max_retries: int = 4,
):
   
    prompt = f"""
    You are an autonomous process control engineer optimizing a ball mill policy `llm_policy(obs)`.

    ### System Dynamics:
    - `pump_speed` (0-100%): Controls `sump_level` (Target: 50.0%). Higher speed lowers level[cite: 1].
    - `sump_water` (0-200 m³/h): Controls cyclone feed density & particle size `product_p80` (Target: 75.0 µm)[cite: 1].
    - `mill_water` (0-100 m³/h): Controls internal mill grinding pulp density[cite: 1].

    ### Previous Dev Seed Performance:
    Average Composite Score: {prev_metrics['avg_score']}
    Per-Seed Traces: {json.dumps(prev_metrics['per_seed'])}

    ### Current Source Code:
    ```python
    {previous_code}
    Task Requirements:Conduct a failure analysis answering:
    What failed in the current code?  
    Which seeds/traces showed the failure?  
    Categorize proposed edit as exactly one of: ["structural", "scalar/config", "bug_fix"].  
    Propose ONE targeted change.  
    Output your response as a valid JSON object matching this schema:{{"failure_analysis": {{"what_failed": "Description of control issue","failing_seeds": [42, 101],"change_type": "structural or scalar/config","next_recommendation": "What to try next"}},"proposed_change": "Single structural or scalar edit description","code": "def llm_policy(obs):\n    ..."}}"""

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model= model,contents=prompt,config={"response_mime_type": "application/json"},)
            data = json.loads(response.text)
            return data
        except errors.ServerError:
            time.sleep(2*attempt)
        except Exception as e:
            print(f"Parsing Error on attempt {attempt+1}: {e}")
            return None

def generate_reports(trials):

# 1. Summary CSV[cite: 2]
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
        "Trial",
        "Avg_Score",
        "Decision",
        "Change_Type",
        "Proposed_Change",
        ])
    for t in trials:
        writer.writerow([
        t["trial"],
        t["avg_score"],
        t["decision"],
        t["failure_analysis"].get("change_type", "N/A"),
        t["proposed_change"],
        ])# 2. Final Report MD[cite: 2]
    best_trial = min(trials, key=lambda x: x["avg_score"])
    with open(FINAL_REPORT_MD, "w", encoding="utf-8") as f:
        f.write("# Heuristic Optimization Final Report\n\n")
        f.write(
            f"- **Total Iterations Run**: {len(trials)}\n"
            f"- **Best Composite Score**: {best_trial['avg_score']} (Trial {best_trial['trial']})\n\n"
        )
        f.write("## Trial Progression\n\n")
        f.write(
            "| Trial | Avg Score | Decision | Change Type | Propose Edit |\n"
        )
        f.write("|---|---|---|---|---|\n")
        for t in trials:
            f.write(
                f"| {t['trial']} | {t['avg_score']} | {t['decision']} | "
                f"{t['failure_analysis'].get('change_type','N/A')} | {t['proposed_change']} |\n"
            )

baseline_code = """
def llm_policy(obs):
    error_level = 50.0 - obs["sump_level"]
    pump_speed = 60.0 - (1.5 * error_level)
    sump_water = 50.0 + (1.0 * error_level)
    mill_water = 30.0
    return pump_speed, sump_water, mill_water"""
best_code = baseline_code
best_score = float("inf")
all_trials = []

scope = {"__builtins__": __builtins__}
exec(best_code, scope)
best_score, initial_traces = run_fixed_seed_eval(scope["llm_policy"])

current_metrics = {"avg_score": best_score, "per_seed": initial_traces}
current_code = best_code
print(f"--- Initial Baseline Score: {best_score} ---")
for trial_idx in range(1, 11):
    print(f"\n================ Iteration {trial_idx} ================")# Step 2-5: Propose edit & get diagnosis JSON[cite: 2]
    agent_response = optimize_heuristic_with_ledger(current_code, current_metrics, trial_num=trial_idx)
    if not agent_response:
        print("Failed to get valid response from agent. Skipping iteration.")
        continue

candidate_code = agent_response["code"]
proposed_change = agent_response["proposed_change"]
failure_analysis = agent_response["failure_analysis"]

# Step 6 & 7: Compile and Evaluate on fixed Dev Seeds[cite: 2]
scope = {"__builtins__": __builtins__}
try:
    exec(candidate_code, scope)
    cand_func = scope["llm_policy"]
    cand_score, cand_traces = run_fixed_seed_eval(cand_func)
    exec_error = None
except Exception as e:
    cand_score = float("inf")
    cand_traces = []
    exec_error = str(e)
    print(f"Execution Error in candidate: {e}")

# Step 9: Evidence-based keep, revise, or rollback decision[cite: 2]
if cand_score < best_score:
    decision = "KEEP"
    print(
        f"✓ KEEP: Score improved from {best_score:.2f} to {cand_score:.2f}"
    )
    best_score = cand_score
    best_code = candidate_code
    current_metrics = {"avg_score": cand_score, "per_seed": cand_traces}
    current_code = candidate_code

    # Save policy
    with open(
        os.path.join(POLICIES_DIR, f"policy_trial_{trial_idx}.py"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write(candidate_code)
else:
    decision = "ROLLBACK"
    print(
        f"✗ ROLLBACK: Candidate score {cand_score:.2f} >= Best score {best_score:.2f}"
    )
    # Roll back to best verified code for next iteration[cite: 2]
    current_code = best_code

# Step 8: Append result to trials.jsonl[cite: 2]
trial_entry = {
    "trial": trial_idx,
    "avg_score": cand_score if exec_error is None else 999999.0,
    "decision": decision,
    "proposed_change": proposed_change,
    "failure_analysis": failure_analysis,
    "per_seed_results": cand_traces,
    "execution_error": exec_error,
}
all_trials.append(trial_entry)

with open(TRIALS_LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps(trial_entry) + "\n")
    generate_reports(all_trials)
    print(f"\nOptimization complete. Artifacts regenerated in {RESULTS_DIR}/.")