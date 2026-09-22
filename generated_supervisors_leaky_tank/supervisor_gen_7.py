def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(step.get("pump_effort", 0.0))
        errors.append(step.get("error", 0.0))
    abs_errors = [abs(e) for e in errors]

    # Robust baseline: median of first up to 5 samples (less contaminated by fault)
    baseline_n = min(5, n)
    baseline_effort = sorted(efforts[:baseline_n])[baseline_n // 2]

    # Recent effort: average of last 3 steps (or all if fewer)
    recent_efforts = efforts[-3:] if n >= 3 else efforts
    avg_recent_effort = sum(recent_efforts) / len(recent_efforts)

    # Recent error: average absolute error of last 3 steps
    recent_abs_errors = abs_errors[-3:] if n >= 3 else abs_errors
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Dynamic threshold based on baseline and recent variability (MAD)
    if n >= 5:
        recent_window = efforts[-5:]
        med = sorted(recent_window)[len(recent_window)//2]
        mad = sorted([abs(e - med) for e in recent_window])[len(recent_window)//2]
    else:
        mad = 0.0
    effort_threshold = baseline_effort * 1.1 + 0.15 + 2.0 * mad

    # Anomaly detection: sustained high effort OR high effort with error
    # Use a simple hysteresis: require two consecutive detections to set flag
    # Since we don't have state, we approximate by checking last two samples
    above_threshold = [e > effort_threshold for e in efforts[-2:]] if n >= 2 else [efforts[-1] > effort_threshold]
    sustained_high = all(above_threshold) if len(above_threshold) > 0 else False
    very_high_effort = avg_recent_effort > 2.5
    high_error = avg_recent_abs_error > 0.15

    anomaly_flag = sustained_high or (very_high_effort and high_error)

    # Setpoint adjustment
    if anomaly_flag:
        # Lower setpoint proportionally to effort excess, with larger cap for severe leaks
        excess = max(0.0, avg_recent_effort - baseline_effort)
        if avg_recent_effort > 3.0:
            adjustment = min(1.0, max(0.3, excess * 0.6))
        else:
            adjustment = min(0.6, max(0.15, excess * 0.4))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {avg_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif avg_recent_effort < baseline_effort * 1.05 and avg_recent_abs_error < 0.1 and active_setpoint < nominal_target:
        # Stable and below nominal: restore cautiously
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.3)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }