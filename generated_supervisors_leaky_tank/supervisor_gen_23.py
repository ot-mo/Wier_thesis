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

    # Adaptive thresholds based on nominal target
    effort_threshold = max(1.5, 1.2 * nominal_target)
    error_threshold = max(0.1, 0.1 * nominal_target)

    # Short window for rapid detection
    short_n = min(3, n)
    short_efforts = efforts[-short_n:]
    short_abs_errors = abs_errors[-short_n:]
    short_effort_exceed = sum(1 for e in short_efforts if e > effort_threshold)
    short_error_exceed = sum(1 for e in short_abs_errors if e > error_threshold)
    short_anomaly = (short_effort_exceed >= 2) or (short_error_exceed >= 2)

    # Long window for persistence
    long_n = min(10, n)
    long_efforts = efforts[-long_n:]
    long_abs_errors = abs_errors[-long_n:]
    long_effort_exceed = sum(1 for e in long_efforts if e > effort_threshold)
    long_error_exceed = sum(1 for e in long_abs_errors if e > error_threshold)
    long_required = max(1, int(0.6 * long_n))
    long_anomaly = (long_effort_exceed >= long_required) or (long_error_exceed >= long_required)

    anomaly_flag = short_anomaly or long_anomaly

    # Compute median effort and average absolute error for diagnosis
    sorted_efforts = sorted(long_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
    avg_abs_error = sum(long_abs_errors) / len(long_abs_errors)

    if anomaly_flag:
        # More aggressive adjustment for severe anomalies
        if median_effort > 2.0 * effort_threshold or avg_abs_error > 2.0 * error_threshold:
            adjustment = 0.3
        else:
            adjustment = 0.2
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.2 and avg_abs_error < 0.05 and active_setpoint < nominal_target:
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