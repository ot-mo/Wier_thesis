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

    # Recent window: last 10 steps (or all if fewer)
    recent_n = min(10, n)
    recent_efforts = efforts[-recent_n:]
    recent_abs_errors = abs_errors[-recent_n:]

    # Compute median of recent efforts
    sorted_recent = sorted(recent_efforts)
    m = len(sorted_recent)
    if m % 2 == 1:
        median_recent_effort = sorted_recent[m // 2]
    else:
        median_recent_effort = (sorted_recent[m // 2 - 1] + sorted_recent[m // 2]) / 2.0

    # Average absolute error of recent window
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Compute effort trend: slope of last 5 efforts (or fewer)
    trend_n = min(5, n)
    if trend_n >= 2:
        x = list(range(trend_n))
        y = efforts[-trend_n:]
        mean_x = sum(x) / trend_n
        mean_y = sum(y) / trend_n
        num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(trend_n))
        den = sum((x[i] - mean_x) ** 2 for i in range(trend_n))
        slope = num / den if den != 0 else 0.0
    else:
        slope = 0.0

    # Compute 75th percentile of recent efforts as a robust high threshold
    if m > 0:
        idx = int(0.75 * (m - 1))
        p75 = sorted_recent[idx]
    else:
        p75 = 0.0

    # Anomaly detection logic
    anomaly_flag = False
    # Condition 1: high effort and significant error
    if median_recent_effort > 1.5 and avg_recent_abs_error > 0.05:
        anomaly_flag = True
    # Condition 2: very high effort regardless of error
    if median_recent_effort > 2.5:
        anomaly_flag = True
    # Condition 3: increasing effort trend and error above threshold
    if slope > 0.1 and avg_recent_abs_error > 0.03:
        anomaly_flag = True
    # Condition 4: consecutive high efforts above p75 + margin
    if n >= 2:
        threshold = p75 + 0.2
        if efforts[-1] > threshold and efforts[-2] > threshold:
            anomaly_flag = True

    # Setpoint adjustment
    if anomaly_flag:
        # More aggressive reduction when effort is high
        if median_recent_effort > 3.0:
            adjustment = min(1.5, max(0.5, (median_recent_effort - 1.0) * 0.5))
        elif median_recent_effort > 2.0:
            adjustment = min(1.0, max(0.3, (median_recent_effort - 1.0) * 0.4))
        else:
            adjustment = min(0.8, max(0.2, (median_recent_effort - 1.0) * 0.3))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {median_recent_effort:.2f}, error {avg_recent_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_recent_effort < 1.2 and avg_recent_abs_error < 0.05 and active_setpoint < nominal_target:
        # Stable and below nominal: restore more aggressively
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.8)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }