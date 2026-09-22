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

    # Use a long window for robust statistics
    long_n = min(20, n)
    recent_efforts = efforts[-long_n:]
    recent_abs_errors = abs_errors[-long_n:]

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

    # Compute median and MAD for abs error
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
    k_effort = 3.0
    k_error = 3.0
    effort_threshold = max(1.5, median_effort + k_effort * mad_effort)
    error_threshold = max(0.1, median_error + k_error * mad_error)

    # Short window for persistence check
    short_n = min(5, n)
    recent_efforts_short = efforts[-short_n:]
    recent_abs_errors_short = abs_errors[-short_n:]

    # Count exceedances in short window
    effort_exceed = sum(1 for e in recent_efforts_short if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors_short if e > error_threshold)
    required = max(1, int(0.6 * short_n))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    anomaly_flag = effort_persistent or error_persistent

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # More aggressive setpoint reduction when anomaly detected
        adjustment = 0.3
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