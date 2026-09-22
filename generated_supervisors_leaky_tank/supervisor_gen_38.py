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

    # Baseline effort: use the first half of the window if available, else overall median
    if n >= 4:
        baseline_efforts = efforts[:max(1, n//2)]
    else:
        baseline_efforts = efforts
    sorted_baseline = sorted(baseline_efforts)
    m = len(sorted_baseline)
    if m % 2 == 1:
        baseline_effort = sorted_baseline[m//2]
    else:
        baseline_effort = (sorted_baseline[m//2 - 1] + sorted_baseline[m//2]) / 2.0

    # Adaptive thresholds
    effort_threshold = max(1.0, baseline_effort * 1.3 + 0.2)
    error_threshold = max(0.05, 0.05 * nominal_target)

    # Short window for fast detection (5 samples)
    short_n = min(5, n)
    recent_efforts_short = efforts[-short_n:]
    recent_abs_errors_short = abs_errors[-short_n:]
    effort_exceed_short = sum(1 for e in recent_efforts_short if e > effort_threshold)
    error_exceed_short = sum(1 for e in recent_abs_errors_short if e > error_threshold)
    required_short = max(1, int(0.6 * short_n))
    effort_persistent_short = effort_exceed_short >= required_short
    error_persistent_short = error_exceed_short >= required_short

    # Long window for slow leaks (10 samples)
    long_n = min(10, n)
    recent_efforts_long = efforts[-long_n:]
    recent_abs_errors_long = abs_errors[-long_n:]
    effort_exceed_long = sum(1 for e in recent_efforts_long if e > effort_threshold)
    error_exceed_long = sum(1 for e in recent_abs_errors_long if e > error_threshold)
    required_long = max(1, int(0.5 * long_n))
    effort_persistent_long = effort_exceed_long >= required_long
    error_persistent_long = error_exceed_long >= required_long

    # Anomaly if both effort and error are persistently high in either window
    anomaly_flag = ((effort_persistent_short and error_persistent_short) or
                    (effort_persistent_long and error_persistent_long))

    # Compute median effort for diagnosis (use long window)
    sorted_efforts = sorted(recent_efforts_long)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
    avg_abs_error = sum(recent_abs_errors_long) / len(recent_abs_errors_long)

    if anomaly_flag:
        # Reduce setpoint conservatively, but only if effort is high
        if median_effort > effort_threshold:
            adjustment = 0.1
            adjusted_setpoint = max(0.0, active_setpoint - adjustment)
            diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
        else:
            adjusted_setpoint = active_setpoint
            diagnosis = f"Anomaly detected but effort moderate: effort {median_effort:.2f}, error {avg_abs_error:.2f}, keeping setpoint."
    elif median_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
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