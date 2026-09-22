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

    # Use a window of up to 20 samples for robust statistics
    window_n = min(20, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Compute median and MAD for effort
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
    abs_dev_efforts = [abs(e - median_effort) for e in recent_efforts]
    sorted_dev_efforts = sorted(abs_dev_efforts)
    if m % 2 == 1:
        mad_effort = sorted_dev_efforts[m // 2]
    else:
        mad_effort = (sorted_dev_efforts[m // 2 - 1] + sorted_dev_efforts[m // 2]) / 2.0

    # Compute median and MAD for absolute error
    sorted_errors = sorted(recent_abs_errors)
    if m % 2 == 1:
        median_error = sorted_errors[m // 2]
    else:
        median_error = (sorted_errors[m // 2 - 1] + sorted_errors[m // 2]) / 2.0
    abs_dev_errors = [abs(e - median_error) for e in recent_abs_errors]
    sorted_dev_errors = sorted(abs_dev_errors)
    if m % 2 == 1:
        mad_error = sorted_dev_errors[m // 2]
    else:
        mad_error = (sorted_dev_errors[m // 2 - 1] + sorted_dev_errors[m // 2]) / 2.0

    # Adaptive thresholds: median + k * MAD, with minimum floors
    effort_threshold = max(1.5, median_effort + 3.0 * mad_effort)
    error_threshold = max(0.1, median_error + 3.0 * mad_error)

    # Check the most recent sample for exceedance
    current_effort = efforts[-1]
    current_abs_error = abs_errors[-1]
    effort_exceed = current_effort > effort_threshold
    error_exceed = current_abs_error > error_threshold

    # Persistence: require 3 consecutive exceedances to flag anomaly
    # Since we cannot maintain state, we check the last 3 samples
    persistence_n = min(3, n)
    recent_efforts_p = efforts[-persistence_n:]
    recent_abs_errors_p = abs_errors[-persistence_n:]
    effort_exceed_count = sum(1 for e in recent_efforts_p if e > effort_threshold)
    error_exceed_count = sum(1 for e in recent_abs_errors_p if e > error_threshold)
    effort_persistent = effort_exceed_count >= persistence_n
    error_persistent = error_exceed_count >= persistence_n

    anomaly_flag = effort_persistent or error_persistent

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # More aggressive setpoint reduction to mitigate leak
        adjustment = 0.5
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