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

    # Use full window for robust baseline statistics
    sorted_efforts = sorted(efforts)
    sorted_abs_errors = sorted(abs_errors)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
        median_abs_error = sorted_abs_errors[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0
        median_abs_error = (sorted_abs_errors[m // 2 - 1] + sorted_abs_errors[m // 2]) / 2.0

    # MAD for effort and error
    mad_effort = sorted([abs(e - median_effort) for e in efforts])[m // 2]
    mad_error = sorted([abs(e - median_abs_error) for e in abs_errors])[m // 2]
    # Scale MAD to be consistent with std for normal distribution
    mad_effort = max(mad_effort, 1e-6)
    mad_error = max(mad_error, 1e-6)

    # Recent window (last 5 steps or fewer)
    recent_n = min(5, n)
    recent_efforts = efforts[-recent_n:]
    recent_abs_errors = abs_errors[-recent_n:]
    recent_median_effort = sorted(recent_efforts)[recent_n // 2]
    recent_median_abs_error = sorted(recent_abs_errors)[recent_n // 2]

    # Robust z-scores
    z_effort = (recent_median_effort - median_effort) / (1.4826 * mad_effort)
    z_error = (recent_median_abs_error - median_abs_error) / (1.4826 * mad_error)

    # Trend detection: slope of error over last min(10, n) steps
    trend_n = min(10, n)
    if trend_n >= 3:
        x_vals = list(range(trend_n))
        y_vals = abs_errors[-trend_n:]
        mean_x = sum(x_vals) / trend_n
        mean_y = sum(y_vals) / trend_n
        num = sum((x_vals[i] - mean_x) * (y_vals[i] - mean_y) for i in range(trend_n))
        den = sum((x_vals[i] - mean_x) ** 2 for i in range(trend_n))
        slope = num / den if den != 0 else 0.0
    else:
        slope = 0.0

    # Anomaly if recent effort or error is significantly above baseline, or error is trending up
    effort_anomaly = z_effort > 3.0
    error_anomaly = z_error > 3.0
    trend_anomaly = slope > 0.01 and recent_median_abs_error > 0.05

    anomaly_flag = effort_anomaly or error_anomaly or trend_anomaly

    if anomaly_flag:
        # Proportional adjustment based on excess effort
        excess = max(0.0, recent_median_effort - median_effort)
        adjustment = min(0.5, 0.2 + 0.1 * excess)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort z={z_effort:.2f}, error z={z_error:.2f}, slope={slope:.3f}, lowering setpoint by {adjustment:.2f}."
    elif recent_median_effort < 1.1 and recent_median_abs_error < 0.03 and active_setpoint < nominal_target:
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