import io
import sys
import os
import time
import numpy as np
from google import genai
from google.genai import errors
import PID

class HeaterSim:
    def __init__(self, dt=0.1, ambient_temp=25.0, loss_coeff=0.02):
        self.dt = dt
        self.ambient_temp = ambient_temp
        self.loss_coeff = loss_coeff        # k_loss
        self.temp = ambient_temp

    def reset(self, initial_temp=25.0):
        self.temp = initial_temp
        return self.temp

    def step(self, power_input):
        power_input = np.clip(power_input, 0.0, 100.0)  # Duty cycle 0-100%
        heat_loss = self.loss_coeff * (self.temp - self.ambient_temp)
        self.temp += (power_input - heat_loss)*self.dt
        return self.temp

client = genai.Client(api_key="AQ.Ab8RN6JcWsxiHj0kqjPGzC6rtAw6okpSc0lExsQ55WxP4235Yg")

def optimize_heuristic(previous_code, metrics):
    prompt = f"""
    You are optimizing a thermal control heuristic in Python for a heater with heat loss.
    
    ### Objectives
    1. Minimize Integrated Absolute Error (IAE).
    2. Eliminate Max Overshoot (°C).
    3. Ensure stable setpoint tracking with reasonable energy usage.
    
    ### Previous Code
    ```python
    {previous_code}
    ```

    ### Requirements
    - Define `llm_policy(setpoint, current_temp) -> float` returning power output (0.0 to 100.0).
    - Return ONLY valid executable Python code enclosed inside a ```python ``` code block.
    - Re-chack that there are NO syntax errors.
    """
    response = None
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt
            )
            break  # Success, exit retry loop
        except errors.ServerError:
            wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s, 8s
            print(f"Google API busy (503). Retrying in {wait_time}s...")
            time.sleep(wait_time)

    # Fallback if all retries fail or response is empty
    raw_text = response.text if (response and response.text) else ""
    clean_code = raw_text.replace("```python", "").replace("```", "").strip()

    if not clean_code:
        print("Warning: Model unavailable or returned empty code. Retaining previous code.")
        return previous_code

    return clean_code

tuned_pid = PID.PIDController(
    Kp=15.0,
    Ki=0.8,
    Kd=2,
    setpoint=100.0,
    output_limits=(0.0, 100.0),
)

baseline_metrics = PID.run_benchmark(HeaterSim, tuned_pid, setpoint=100.0)

print("--- Custom PIDController Baseline Metrics ---")
print(f"IAE: {baseline_metrics['IAE']} | Energy: {baseline_metrics['Total_Energy']} | Max Overshoot: {baseline_metrics['Max_Overshoot']}°C\n")

output_dir = "generated_policies"
os.makedirs(output_dir, exist_ok=True)

current_llm_code = """
def llm_policy(setpoint, current_temp):
    error = setpoint - current_temp
    return 2.0 * error  # Simple P heuristic
"""

for gen in range(3):
    gen_num = gen + 1
    file_path = os.path.join(output_dir, f"policy_gen_{gen_num}.py")

    # Save the current LLM code to a new .py file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(current_llm_code)

    scope = {"__builtins__": __builtins__}
    exec(current_llm_code, scope)
    llm_func = scope["llm_policy"]

    llm_metrics = PID.run_benchmark(HeaterSim, llm_func, setpoint=100.0)
    print(f"--- LLM Generation {gen+1} Metrics ---")
    print(f"IAE: {llm_metrics['IAE']} | Energy: {llm_metrics['Total_Energy']} | Max Overshoot: {llm_metrics['Max_Overshoot']}°C")

    current_llm_code = optimize_heuristic(current_llm_code, llm_metrics)