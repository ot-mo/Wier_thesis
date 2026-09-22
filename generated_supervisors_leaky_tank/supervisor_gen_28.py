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

    # Baseline effort: use the first few samples if available, else nominal_target
    baseline_n = min(5, n)
    baseline_effort = sum(efforts[:baseline_n]) / baseline_n if baseline_n > 0 else nominal_target
    # Ensure baseline is not too low (avoid division by zero or unrealistic)
    baseline_effort = max(baseline_effort, 0.5)

    # Windows
    short_n = min(3, n)
    long_n = min(10, n)
    recent_efforts_short = efforts[-short_n:]
    recent_abs_errors_short = abs_errors[-short_n:]
    recent_efforts_long = efforts[-long_n:]
    recent_abs_errors_long = abs_errors[-long_n:]

    # Adaptive thresholds based on baseline and nominal_target
    # Effort threshold: baseline + margin, but not less than a fraction of nominal
    effort_threshold = max(baseline_effort * 1.3, nominal_target * 0.8, 1.0)
    # Error threshold: relative to nominal_target, but with a floor
    error_threshold = max(0.05 * nominal_target, 0.02)

    # Count exceedances
    effort_exceed_short = sum(1 for e in recent_efforts_short if e > effort_threshold)
    error_exceed_short = sum(1 for e in recent_abs_errors_short if e > error_threshold)
    required_short = max(1, int(0.6 * short_n))
    effort_persistent_short = effort_exceed_short >= required_short
    error_persistent_short = error_exceed_short >= required_short

    effort_exceed_long = sum(1 for e in recent_efforts_long if e > effort_threshold)
    error_exceed_long = sum(1 for e in recent_abs_errors_long if e > error_threshold)
    required_long = max(1, int(0.6 * long_n))
    effort_persistent_long = effort_exceed_long >= required_long
    error_persistent_long = error_exceed_long >= required_long

    # Anomaly if either effort or error is persistently high in either window
    anomaly_flag = (effort_persistent_short or error_persistent_short or
                    effort_persistent_long or error_persistent_long)

    # Compute median effort and average error for diagnosis and adjustment
    sorted_efforts = sorted(recent_efforts_long)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
    avg_abs_error = sum(recent_abs_errors_long) / len(recent_abs_errors_long)

    if anomaly_flag:
        # Proportional adjustment based on how much effort exceeds baseline
        excess = max(0.0, median_effort - baseline_effort)
        # Scale adjustment: 0.1 per unit excess, capped at 0.5
        adjustment = min(0.5, max(0.1, 0.1 * excess))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f} (baseline {baseline_effort:.2f}), error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < baseline_effort * 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
        # Restore setpoint gradually when stable and below nominal
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.1)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }