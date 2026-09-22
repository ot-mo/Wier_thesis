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

    # Use a shorter recent window for faster response
    recent_n = min(5, n)
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

    # Compute error trend: slope of last 5 absolute errors
    if trend_n >= 2:
        y_err = abs_errors[-trend_n:]
        mean_y_err = sum(y_err) / trend_n
        num_err = sum((x[i] - mean_x) * (y_err[i] - mean_y_err) for i in range(trend_n))
        slope_err = num_err / den if den != 0 else 0.0
    else:
        slope_err = 0.0

    # Adaptive thresholds based on recent statistics
    # Use median effort and average error to set dynamic thresholds
    # Baseline effort is around 0.9-1.0, so threshold for high effort is median + margin
    effort_threshold = max(1.2, median_recent_effort * 1.1)
    error_threshold = max(0.03, avg_recent_abs_error * 1.5)

    # Anomaly detection logic with multiple indicators
    anomaly_flag = False
    indicators = 0

    # Indicator 1: high effort relative to threshold
    if median_recent_effort > effort_threshold:
        indicators += 1
    # Indicator 2: significant error
    if avg_recent_abs_error > error_threshold:
        indicators += 1
    # Indicator 3: increasing effort trend
    if slope > 0.05:
        indicators += 1
    # Indicator 4: increasing error trend
    if slope_err > 0.01:
        indicators += 1
    # Indicator 5: very high effort (absolute)
    if median_recent_effort > 2.0:
        indicators += 1
    # Indicator 6: very high error (absolute)
    if avg_recent_abs_error > 0.1:
        indicators += 1

    # Require at least 2 indicators for anomaly, but if effort is very high, 1 is enough
    if indicators >= 2 or median_recent_effort > 2.5 or avg_recent_abs_error > 0.15:
        anomaly_flag = True

    # Setpoint adjustment: more conservative, proportional to severity
    if anomaly_flag:
        # Compute severity based on effort and error
        severity = max(0.0, (median_recent_effort - 1.0) * 0.5 + avg_recent_abs_error * 2.0)
        # Cap adjustment to avoid large jumps
        adjustment = min(0.5, max(0.1, severity * 0.3))
        adjusted_setpoint = active_setpoint - adjustment
        # Ensure setpoint does not go below a safe minimum (e.g., 0.0)
        adjusted_setpoint = max(0.0, adjusted_setpoint)
        diagnosis = f"Anomaly detected: effort {median_recent_effort:.2f}, error {avg_recent_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_recent_effort < 1.1 and avg_recent_abs_error < 0.03 and active_setpoint < nominal_target:
        # Stable and below nominal: restore gradually
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.5)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }