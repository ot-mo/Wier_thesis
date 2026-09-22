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

    # Dual windows: short for fast detection, long for slow leaks
    short_n = min(3, n)
    long_n = min(10, n)
    recent_efforts_short = efforts[-short_n:]
    recent_abs_errors_short = abs_errors[-short_n:]
    recent_efforts_long = efforts[-long_n:]
    recent_abs_errors_long = abs_errors[-long_n:]

    # Adaptive thresholds
    effort_threshold = max(1.5, 0.9 * nominal_target)
    error_threshold = max(0.1, 0.1 * nominal_target)

    # Count exceedances in short window
    effort_exceed_short = sum(1 for e in recent_efforts_short if e > effort_threshold)
    error_exceed_short = sum(1 for e in recent_abs_errors_short if e > error_threshold)
    required_short = max(1, int(0.7 * short_n))
    effort_persistent_short = effort_exceed_short >= required_short
    error_persistent_short = error_exceed_short >= required_short

    # Count exceedances in long window
    effort_exceed_long = sum(1 for e in recent_efforts_long if e > effort_threshold)
    error_exceed_long = sum(1 for e in recent_abs_errors_long if e > error_threshold)
    required_long = max(1, int(0.7 * long_n))
    effort_persistent_long = effort_exceed_long >= required_long
    error_persistent_long = error_exceed_long >= required_long

    anomaly_flag = (effort_persistent_short or error_persistent_short or
                    effort_persistent_long or error_persistent_long)

    # Compute median effort for diagnosis (use long window for stability)
    sorted_efforts = sorted(recent_efforts_long)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
    avg_abs_error = sum(recent_abs_errors_long) / len(recent_abs_errors_long)

    if anomaly_flag:
        adjustment = 0.2
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
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