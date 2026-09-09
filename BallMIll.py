import time

import numpy as np
import os
from google import genai
from google.genai import errors
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("API_KEY")

client = genai.Client(api_key=api_key)

class BallMillSimulator:

    def __init__(self, dt=0.1):  # dt in minutes
        self.dt = dt
        self.reset()

    def reset(self):
        # State variables
        self.sump_level = 50.0  # % (0 - 100)
        self.sump_solids = 25.0  # tonnes
        self.sump_water = 25.0  # m^3
        self.mill_load = 40.0  # tonnes of ore in mill
        self.product_p80 = 75.0  # Target particle size in microns
        self.circulating_load = 300.0  # t/h underflow[cite: 1]
        return self.get_measurements()

    def step(
        self,
        pump_speed,
        sump_water_in,
        mill_water_in,
        fresh_feed=300.0,
        ore_hardness=1.0,
    ):
        """Advances circuit dynamics by one timestep (dt).

        MVs:
          pump_speed: 0 to 100 %[cite: 1]
          sump_water_in: 0 to 200 m^3/h[cite: 1]
          mill_water_in: 0 to 100 m^3/h[cite: 1]
        DVs:
          fresh_feed: Fresh ore feed rate (t/h)[cite: 1]
          ore_hardness: Hardness factor multiplier (1.0 = nominal)[cite: 1]
        """
        # Clamp actuators (MVs)[cite: 1]
        pump_speed = np.clip(pump_speed, 0.0, 100.0)
        sump_water_in = np.clip(sump_water_in, 0.0, 200.0)
        mill_water_in = np.clip(mill_water_in, 0.0, 100.0)

        # --- 1. Mill Dynamics ---
        # Mill output rate depends on load and water[cite: 1]
        mill_discharge_rate = self.mill_load * 4.0  # t/h
        d_mill = (
            self.circulating_load - mill_discharge_rate
        ) * (self.dt / 60.0)
        self.mill_load = max(5.0, self.mill_load + d_mill)

        # Mill power curve (parabolic with overload penalty)[cite: 1]
        optimal_load = 45.0
        power_factor = 1.0 - ((self.mill_load - optimal_load) / optimal_load) ** 2
        mill_power_kw = max(200.0, 3500.0 * power_factor)  # kW draw

        # --- 2. Sump Dynamics ---
        # Inflows: Fresh Feed + Mill Discharge + Added Water[cite: 1]
        solids_in = fresh_feed + mill_discharge_rate  # t/h
        water_in = sump_water_in + mill_water_in  # m^3/h

        # Outflow via slurry pump[cite: 1]
        total_vol = self.sump_solids / 2.7 + self.sump_water  # m^3 (SG_ore = 2.7)
        pump_capacity_m3h = pump_speed * 8.0  # max 800 m^3/h
        pump_out_vol = min(total_vol * 10, pump_capacity_m3h) * (self.dt / 60.0)

        solids_out = pump_out_vol * (self.sump_solids / max(0.1, total_vol))
        water_out = pump_out_vol * (self.sump_water / max(0.1, total_vol))

        # Update Sump Inventory
        self.sump_solids = max(
            0.0, self.sump_solids + (solids_in * (self.dt / 60.0)) - solids_out
        )
        self.sump_water = max(
            0.0, self.sump_water + (water_in * (self.dt / 60.0)) - water_out
        )

        sump_capacity_m3 = 60.0
        current_vol = (self.sump_solids / 2.7) + self.sump_water
        self.sump_level = np.clip((current_vol / sump_capacity_m3) * 100.0, 0.0, 100.0)

        # Cyclone Feed Density (% solids by mass)[cite: 1]
        tot_mass = self.sump_solids + self.sump_water
        cyclone_density = (
            (self.sump_solids / tot_mass * 100.0) if tot_mass > 0 else 0.0
        )

        # --- 3. Cyclone Classification Dynamics ---
        # Product P80 affected by feed density, flow rate, and hardness[cite: 1]
        density_effect = 1.0 + 0.02 * (cyclone_density - 50.0)
        flow_effect = 1.0 - 0.01 * (pump_speed - 60.0)
        self.product_p80 = np.clip(
            75.0 * density_effect * flow_effect * ore_hardness, 30.0, 180.0
        )

        # Underflow split (Circulating Load)[cite: 1]
        split_to_underflow = np.clip(0.5 + 0.005 * (cyclone_density - 50.0), 0.3, 0.8)
        self.circulating_load = solids_out * (60.0 / self.dt) * split_to_underflow

        return self.get_measurements()

    def get_measurements(self):
        """Sensors available to the control algorithm[cite: 1]."""
        return {
            "sump_level": self.sump_level,
            "product_p80": self.product_p80,
            "mill_power": getattr(self, "mill_power", 3000.0),
            "circulating_load": self.circulating_load,
        }


def run_mill_benchmark(policy_func, duration_steps=120, dt=0.1):
    """Runs a full simulation trial and compiles telemetry metrics for the LLM."""
    sim = BallMillSimulator(dt=dt)
    obs = sim.reset()

    # Targets[cite: 1]
    target_level = 50.0  # %
    target_p80 = 75.0  # microns

    iae_level = 0.0
    iae_p80 = 0.0
    total_energy_kwh = 0.0
    violations = 0

    for step in range(duration_steps):
        # Introduce ore hardness disturbance mid-run[cite: 1]
        hardness = 1.25 if step > 60 else 1.0

        # Query LLM policy
        pump_speed, sump_water, mill_water = policy_func(obs)

        # Step physics
        obs = sim.step(
            pump_speed,
            sump_water,
            mill_water,
            fresh_feed=320.0,
            ore_hardness=hardness,
        )

        # Accumulate metrics
        iae_level += abs(obs["sump_level"] - target_level) * dt
        iae_p80 += abs(obs["product_p80"] - target_p80) * dt
        total_energy_kwh += (obs["mill_power"] * (dt / 60.0))

        # Penalty conditions: Sump overflow/empty or Mill overload[cite: 1]
        if obs["sump_level"] > 90.0 or obs["sump_level"] < 15.0:
            violations += 1
        if obs["mill_power"] < 1500.0:  # Mill plugging indicator[cite: 1]
            violations += 1

    return {
        "IAE_Sump_Level": round(iae_level, 2),
        "IAE_Product_P80": round(iae_p80, 2),
        "Total_Energy_kWh": round(total_energy_kwh, 2),
        "Constraint_Violations": violations,
        "Final_P80": round(obs["product_p80"], 2),
        "Final_Sump_Level": round(obs["sump_level"], 2),
    }

def optimize_heuristic(previous_code, metrics):
    prompt = f"""
    You are optimizing a Python control heuristic `llm_policy(obs)` for a reverse ball mill grinding circuit.

### System Physics & Control Matrix:
1. `pump_speed` (0-100%): Primary actuator for `sump_level` (Target: 50.0%). Increasing speed lowers sump level.
2. `sump_water` (0-200 m³/h): Controls cyclone feed density, which directly impacts product particle size `product_p80` (Target: 75.0 µm).
3. `mill_water` (0-100 m³/h): Controls internal mill pulp density and grinding efficiency.

### Observations Input (`obs`):
- `obs["sump_level"]`: Current level (%) [Target: 50.0]
- `obs["product_p80"]`: Current particle size (µm) [Target: 75.0]
- `obs["mill_power"]`: Mill draw (kW)
- `obs["circulating_load"]`: Mill underflow (t/h)

### Previous Performance Metrics:
- Sump Level IAE: {metrics.get('IAE_Sump_Level')} (Lower is better)
- Product P80 IAE: {metrics.get('IAE_Product_P80')} (CRITICAL: Currently very high! Needs aggressive improvement)
- Total Energy: {metrics.get('Total_Energy_kWh')} kWh
- Constraint Violations: {metrics.get('Constraint_Violations')}

### Task:
Write a revised `llm_policy(obs)` function returning a tuple `(pump_speed, sump_water, mill_water)`.
- Use dynamic coupling: Adjust `sump_water` based on `product_p80` error to fix particle size.
- Adjust `pump_speed` using proportional-integral action on `sump_level` error.
- Output ONLY raw executable Python code. Do NOT use markdown blocks or commentary.

### Current Code:
{previous_code}
"""

    response = None
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash", contents=prompt
            )
            break
        except errors.ServerError:
            time.sleep(2**attempt)

    raw_text = response.text if (response and response.text) else ""
    clean_code = raw_text.replace("```python", "").replace("```", "").strip()

    return clean_code if clean_code else previous_code

current_llm_code = """
def llm_policy(obs):
    # Basic proportional control baseline
    error_level = 50.0 - obs["sump_level"]
    
    # Calculate MVs
    pump_speed = 60.0 - (1.5 * error_level)
    sump_water = 50.0 + (1.0 * error_level)
    mill_water = 30.0
    
    return pump_speed, sump_water, mill_water
"""

output_dir = "generated_policies_ball_mill"
os.makedirs(output_dir, exist_ok=True)

def calculate_score(metrics: dict) -> float:
    # Heavily penalize safety/constraint violations
    penalty = metrics.get("Constraint_Violations", 0) * 1000.0
    return metrics["IAE_Sump_Level"] + metrics["IAE_Product_P80"] + penalty

# Initialize tracking variables
best_score = float("inf")
best_code = current_llm_code  # Your baseline code string

patience = 6  # Stop if no improvement for 6 consecutive generations
no_improve_count = 0
max_generations = 30

for gen in range(max_generations):
    gen_num = gen + 1
    print(f"\n--- Generation {gen_num} ---")

    # 2. Compile and benchmark candidate code
    scope = {"__builtins__": __builtins__}
    exec(current_llm_code, scope)
    llm_func = scope["llm_policy"]
    llm_metrics = run_mill_benchmark(llm_func)
    candidate_score = calculate_score(llm_metrics)
    
    print(f"Candidate Score: {candidate_score:.2f} | Sump IAE: {llm_metrics['IAE_Sump_Level']} | P80 IAE: {llm_metrics['IAE_Product_P80']}")

    # 3. Elitism Check: Update best policy only if performance improved
    if candidate_score < best_score:
        print(f"✓ NEW BEST FOUND! (Score improved: {best_score:.2f} -> {candidate_score:.2f})")
        best_score = candidate_score
        best_code = current_llm_code
        no_improve_count = 0
        
        # Save verified improvement to disk
        file_path = os.path.join(output_dir, f"best_policy_gen_{gen_num}.py")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(best_code)
    else:
        no_improve_count += 1
        print(f"✗ Candidate rejected. (Retaining previous best score: {best_score:.2f})")
    if no_improve_count >= patience:
        print(
            f"\nConvergence reached at Generation {gen_num}. Stopping early."
        )
        break

    # 4. Mutate using the BEST code found so far, NOT the failed candidate
    current_llm_code = optimize_heuristic(best_code, llm_metrics)
