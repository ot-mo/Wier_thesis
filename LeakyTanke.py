import time
import os
import json
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

from pydantic import BaseModel
from google import genai
from google.genai import errors
from google.genai import types
from openai import OpenAI

from dotenv import load_dotenv

load_dotenv()

# 1. Initialize the Gemini Client
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)
# 2. Define the response schema using Pydantic
class SupervisoryDecision(BaseModel):
    diagnosis: str
    adjusted_setpoint: float
    anomaly_flag: bool

# -------------------------------------------------------------------
# 1. PLANT SIMULATOR & LOW-LEVEL PID (Sub-Second Loop)
# -------------------------------------------------------------------
class LeakyTank:
    def __init__(self, area=2.0, outflow_coeff=0.5):
        self.area = area
        self.c = outflow_coeff
        self.h = 2.0  # Current water level (m)
        self.d = 0.0  # Unmeasured leak (disturbance)

    def step(self, u, dt=0.1):
        # Physics Euler Integration
        gravity_out = self.c * np.sqrt(max(self.h, 0.0))
        dhdt = (u - gravity_out - self.d) / self.area
        self.h = max(0.0, self.h + dhdt * dt)
        return self.h

class LocalPID:
    def __init__(self, Kp=2.0, Ki=0.5, Kd=0.1, dt=0.1):
        self.Kp, self.Ki, self.Kd, self.dt = Kp, Ki, Kd, dt
        self.integral = 0.0
        self.prev_error = 0.0

    def compute(self, setpoint, pv):
        error = setpoint - pv
        self.integral += error * self.dt
        derivative = (error - self.prev_error) / self.dt
        self.prev_error = error
        u = self.Kp * error + self.Ki * self.integral + self.Kd * derivative
        return max(0.0, min(10.0, u))  # Pump limit: [0, 10]

def compute_textual_loss(history_window):
    avg_u = sum(step["pump_effort"] for step in history_window) / len(history_window)
    max_u = max(step["pump_effort"] for step in history_window)
    error_h = abs(history_window[-1]["water_level"] - history_window[-1]["target_setpoint"])
    
    loss_report = {
        "tracking_error": round(error_h, 3),
        "avg_actuator_effort": round(avg_u, 3),
        "actuator_saturated": max_u > 3.5,
        "efficiency_loss_high": avg_u > 2.0
    }
    return loss_report
# -------------------------------------------------------------------
# 2. LLM SUPERVISORY AGENT (Macro Loop)
# -------------------------------------------------------------------
def llm_supervisory_agent(telemetry_summary, current_setpoint, nominal_target=3.0, max_retries=3):
    prompt = f"""
    You are an industrial supervisory control agent monitoring a water tank system.

    Nominal Target Setpoint: {nominal_target} m
    Current Active Setpoint: {current_setpoint} m
    Telemetry Summary (Last 2 seconds): {json.dumps(telemetry_summary)}

    Task:
    1. Evaluate system dynamics (water level error and pump effort).
    2. Diagnose behavior:
       - IF ANOMALY/LEAK DETECTED (high pump effort, dropping level): Safely LOWER setpoint to reduce stress.
       - IF SYSTEM STABILIZED / RECOVERED (low pump effort, error near 0, no leak): GRADUALLY RESTORE setpoint back toward nominal ({nominal_target} m).
       - OTHERWISE: Maintain current active setpoint.

    You must output strictly JSON matching this structure:
    {{
      "diagnosis": "string explanation of system state",
      "adjusted_setpoint": float,
      "anomaly_flag": boolean
    }}
    """

    for attempt in range(1, max_retries + 1):
        try:
            # Short sleep to respect rate limits
            time.sleep(0.3)

            response = client.chat.completions.create(
                model="deepseek-chat", # Use "deepseek-reasoner" if testing DeepSeek-R1
                messages=[
                    {"role": "system", "content": "You are an industrial process supervisor. Respond ONLY with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )

            raw_text = response.choices[0].message.content
            
            # Validate output structure via Pydantic
            validated_decision = SupervisoryDecision.model_validate_json(raw_text)
            return validated_decision.model_dump()

        except Exception as e:
            print(f"[DEEPSEEK API ERROR - Attempt {attempt}/{max_retries}]: {e}")
            time.sleep(1.0 * attempt)

    print(f"[CRITICAL] All {max_retries} retries failed for this cycle. Preserving active setpoint.")
    return {
        "diagnosis": "API_FAILURE: DeepSeek endpoint unreachable. Preserving setpoint.",
        "adjusted_setpoint": current_setpoint,
        "anomaly_flag": True
    }

# -------------------------------------------------------------------
# 3. MAIN SIMULATION EXECUTION
# -------------------------------------------------------------------
tank = LeakyTank()
pid = LocalPID()

nominal_setpoint = 3.0
active_setpoint = nominal_setpoint
dt = 0.1
sim_time = 30.0  # seconds
simulation_steps = int(sim_time / dt)
macro_cycle_steps = 30  # Trigger LLM every 2 seconds (20 * 0.1s)

# Telemetry logging arrays
time_hist = []
level_hist = []
setpoint_hist = []
pump_hist = []
leak_hist = []
telemetry_buffer = []
llm_events = []  # To store (time, diagnosis, setpoint)

llm_log = []

print("Starting Toy Simulation...")
for t_step in range(simulation_steps):
    t = round(t_step * dt, 1)
    
    # Inject OOD Disturbance at t = 3.0s
    if t == 5.0:
        print("\n=== DISTURBANCE INJECTED: Sudden Pipe Leak (d = 3.0) ===\n")
        tank.d = 3.0

    elif t == 16.0:
            print("\n=== DISTURBANCE FIXED: Pipe Has been fixed (d = 0.0) ===\n")
            tank.d = 0.0

    # 1. Low-level Execution
    u = pid.compute(active_setpoint, tank.h)
    h = tank.step(u, dt)

    # Log step data
    time_hist.append(t)
    level_hist.append(h)
    setpoint_hist.append(active_setpoint)
    pump_hist.append(u)
    leak_hist.append(tank.d)

    # Record telemetry
    telemetry_buffer.append({
        "time": t,
        "level": round(h, 2),
        "pump_effort": round(u, 2),
        "error": round(active_setpoint - h, 2)
    })
    
    # 2. Macro-Level LLM Trigger
    if t_step > 0 and t_step % macro_cycle_steps == 0:
        decision = llm_supervisory_agent(
            telemetry_summary=telemetry_buffer[-macro_cycle_steps:],
            current_setpoint=active_setpoint,
            nominal_target=nominal_setpoint
        )
        llm_log.append({
            "time_s": t,
            "water_level_m": round(tank.h, 3),
            "pump_effort": round(u, 3),
            "adjusted_setpoint": decision["adjusted_setpoint"],
            "anomaly_flag": decision["anomaly_flag"],
            "diagnosis": decision["diagnosis"]
        })
        # Guardrail bounds [0.5m, 4.0m]
        proposed_sp = decision['adjusted_setpoint']
        active_setpoint = max(0.5, min(4.0, proposed_sp))

        print(f"[t={t}s] LLM Diagnosis: {decision['diagnosis']}")
        print(f"        Updated Setpoint: {active_setpoint} m")

        llm_events.append((t, decision['diagnosis'], active_setpoint))

        # -------------------------------------------------------------------
# 5. MATPLOTLIB VISUALIZATION
# -------------------------------------------------------------------
results_dir = "results"
os.makedirs(results_dir, exist_ok=True)

# 1. Save as formatted JSON (Preserves full text explanations cleanly)
json_path = os.path.join(results_dir, "llm_decisions.json")
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(llm_log, f, indent=2, ensure_ascii=False)

print(f"[SUCCESS] LLM JSON log saved to: {json_path}")

# 2. Save as CSV (Easy to open in Excel or load into Pandas)
df_llm = pd.DataFrame(llm_log)
csv_path = os.path.join(results_dir, "llm_decisions.csv")
df_llm.to_csv(csv_path, index=False)

print(f"[SUCCESS] LLM CSV log saved to: {csv_path}")

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

# Subplot 1: Process Variable & Setpoints
ax1.plot(time_hist, level_hist, label="Water Level $h(t)$", color="blue", linewidth=2)
ax1.plot(time_hist, setpoint_hist, label="Active Setpoint $u_{\text{sp}}(t)$", color="red", linestyle="--", linewidth=1.8)
ax1.axhline(nominal_setpoint, label="Nominal Target (3.0 m)", color="gray", linestyle=":", alpha=0.7)

# Shade Disturbance Region
ax1.fill_between(time_hist, 0, max(setpoint_hist) + 0.5, where=(np.array(leak_hist) > 0), 
                 color="orange", alpha=0.15, label="Leak Disturbance Active")

ax1.set_ylabel("Water Level (m)")
ax1.set_title("LLM Supervisory Control: Response to Severe Leak & Recovery")
ax1.legend(loc="upper right")
ax1.grid(True, linestyle="--", alpha=0.6)

# Subplot 2: Control Effort & Disturbance Rate
ax2.plot(time_hist, pump_hist, label="Pump Effort $u(t)$", color="green", linewidth=1.5)
ax2.plot(time_hist, leak_hist, label="Leak Rate $d(t)$", color="darkred", linestyle="-.", linewidth=1.5)

ax2.set_xlabel("Time (seconds)")
ax2.set_ylabel("Control Effort / Flow Rate")
ax2.legend(loc="upper right")
ax2.grid(True, linestyle="--", alpha=0.6)

plt.tight_layout()
save_path = os.path.join(results_dir, "llm_supervisory_response.png")
plt.savefig(save_path, dpi=300, bbox_inches="tight")
print(f"\n[SUCCESS] Plot saved to: {save_path}")
plt.show()