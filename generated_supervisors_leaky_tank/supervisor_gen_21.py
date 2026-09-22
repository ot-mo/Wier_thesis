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

    # Use a longer window for robust statistics, but cap at 10
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Adaptive thresholds based on recent statistics
    # For effort: use median + 2.5 * MAD (robust to outliers)
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
    mad_effort = sorted([abs(e - median_effort) for e in recent_efforts])
    if m % 2 == 1:
        mad_effort_val = mad_effort[m // 2]
    else:
        mad_effort_val = (mad_effort[m // 2 - 1] + mad_effort[m // 2]) / 2.0
    effort_threshold = max(1.5, median_effort + 2.5 * mad_effort_val)

    # For error: use median + 2.5 * MAD
    sorted_errors = sorted(recent_abs_errors)
    if m % 2 == 1:
        median_error = sorted_errors[m // 2]
    else:
        median_error = (sorted_errors[m // 2 - 1] + sorted_errors[m // 2]) / 2.0
    mad_error = sorted([abs(e - median_error) for e in recent_abs_errors])
    if m % 2 == 1:
        mad_error_val = mad_error[m // 2]
    else:
        mad_error_val = (mad_error[m // 2 - 1] + mad_error[m // 2]) / 2.0
    error_threshold = max(0.08, median_error + 2.5 * mad_error_val)

    # Count exceedances in the recent window
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)

    # Require at least 60% of the window to exceed thresholds
    required = max(1, int(0.6 * window_n))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    # Trend detection: slope of error over the window (positive slope indicates worsening)
    # Use simple linear regression slope
    if window_n >= 3:
        x_vals = list(range(window_n))
        mean_x = sum(x_vals) / window_n
        mean_y = sum(recent_abs_errors) / window_n
        num = sum((x_vals[i] - mean_x) * (recent_abs_errors[i] - mean_y) for i in range(window_n))
        den = sum((x_vals[i] - mean_x) ** 2 for i in range(window_n))
        slope = num / den if den != 0 else 0.0
    else:
        slope = 0.0

    # Anomaly if persistent exceedance or strong positive trend in error
    anomaly_flag = effort_persistent or error_persistent or (slope > 0.02 and median_error > 0.05)

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # Scale adjustment based on severity: more aggressive for larger errors/efforts
        severity = max(avg_abs_error / max(0.1, nominal_target), (median_effort - 1.0) / max(1.0, nominal_target))
        adjustment = min(0.3, max(0.05, 0.1 + 0.2 * severity))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, slope {slope:.3f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
        # Restore setpoint gradually, but not too fast
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.05)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }