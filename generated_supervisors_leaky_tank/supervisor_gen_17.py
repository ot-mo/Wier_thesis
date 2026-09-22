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

    # Use a longer window for baseline statistics
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Compute robust statistics
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0

    sorted_errors = sorted(recent_abs_errors)
    if m % 2 == 1:
        median_error = sorted_errors[m // 2]
    else:
        median_error = (sorted_errors[m // 2 - 1] + sorted_errors[m // 2]) / 2.0

    # Compute MAD for effort and error
    effort_devs = [abs(e - median_effort) for e in recent_efforts]
    sorted_effort_devs = sorted(effort_devs)
    if m % 2 == 1:
        mad_effort = sorted_effort_devs[m // 2]
    else:
        mad_effort = (sorted_effort_devs[m // 2 - 1] + sorted_effort_devs[m // 2]) / 2.0

    error_devs = [abs(e - median_error) for e in recent_abs_errors]
    sorted_error_devs = sorted(error_devs)
    if m % 2 == 1:
        mad_error = sorted_error_devs[m // 2]
    else:
        mad_error = (sorted_error_devs[m // 2 - 1] + sorted_error_devs[m // 2]) / 2.0

    # Adaptive thresholds: median + 3*MAD, with minimums
    effort_threshold = max(1.2, median_effort + 3.0 * mad_effort)
    error_threshold = max(0.05, median_error + 3.0 * mad_error)

    # Fast detection: last 3 steps
    fast_n = min(3, n)
    fast_efforts = efforts[-fast_n:]
    fast_abs_errors = abs_errors[-fast_n:]
    fast_effort_exceed = sum(1 for e in fast_efforts if e > effort_threshold)
    fast_error_exceed = sum(1 for e in fast_abs_errors if e > error_threshold)
    fast_anomaly = (fast_effort_exceed >= 2) or (fast_error_exceed >= 2)

    # Slow detection: last 10 steps, require 60% exceedance
    slow_effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    slow_error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)
    required = max(1, int(0.6 * window_n))
    slow_anomaly = (slow_effort_exceed >= required) or (slow_error_exceed >= required)

    anomaly_flag = fast_anomaly or slow_anomaly

    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # More aggressive reduction for severe anomalies
        if fast_anomaly:
            adjustment = 0.2
        else:
            adjustment = 0.1
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and avg_abs_error < 0.05 and active_setpoint < nominal_target:
        # More aggressive restoration
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.15)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }