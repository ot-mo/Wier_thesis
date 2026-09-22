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

    # Robust baseline: average of the lowest 30% of efforts (at least 3 samples)
    sorted_efforts = sorted(efforts)
    k = max(3, int(0.3 * n))
    baseline_effort = sum(sorted_efforts[:k]) / k

    # Recent effort: median of last 5 samples (or all if fewer)
    recent_efforts = efforts[-5:] if n >= 5 else efforts
    sorted_recent = sorted(recent_efforts)
    m = len(sorted_recent)
    if m % 2 == 1:
        median_recent_effort = sorted_recent[m // 2]
    else:
        median_recent_effort = (sorted_recent[m // 2 - 1] + sorted_recent[m // 2]) / 2.0

    # Recent error: average absolute error of last 5 samples
    recent_abs_errors = abs_errors[-5:] if n >= 5 else abs_errors
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Recent variability (standard deviation of last 5 efforts)
    if len(recent_efforts) > 1:
        mean_recent = sum(recent_efforts) / len(recent_efforts)
        var_recent = sum((e - mean_recent) ** 2 for e in recent_efforts) / len(recent_efforts)
        std_recent = var_recent ** 0.5
    else:
        std_recent = 0.0

    # Adaptive threshold
    effort_threshold = baseline_effort * 1.10 + 0.15 + 0.5 * std_recent

    # Check last two consecutive efforts above threshold
    last_two_high = False
    if n >= 2:
        last_two_high = efforts[-1] > effort_threshold and efforts[-2] > effort_threshold
    elif n == 1:
        last_two_high = efforts[-1] > effort_threshold

    # Additional condition: very high median effort and high error
    very_high_effort = median_recent_effort > baseline_effort * 1.5
    high_error = avg_recent_abs_error > 0.1

    anomaly_flag = last_two_high or (very_high_effort and high_error)

    # Setpoint adjustment
    if anomaly_flag:
        excess = max(0.0, median_recent_effort - baseline_effort)
        adjustment = min(0.5, max(0.1, excess * 0.3))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: median effort {median_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_recent_effort < baseline_effort * 1.1 and avg_recent_abs_error < 0.15 and active_setpoint < nominal_target:
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