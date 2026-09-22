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

    # Long-term baseline: use the first 10 samples if available, else all
    baseline_n = min(10, n)
    baseline_effort = sorted(efforts[:baseline_n])[baseline_n // 2]

    # Recent window: last 10 steps (or all if fewer)
    recent_n = min(10, n)
    recent_efforts = efforts[-recent_n:]
    recent_abs_errors = abs_errors[-recent_n:]

    # Robust median of recent efforts
    sorted_recent = sorted(recent_efforts)
    m = len(sorted_recent)
    if m % 2 == 1:
        median_recent_effort = sorted_recent[m // 2]
    else:
        median_recent_effort = (sorted_recent[m // 2 - 1] + sorted_recent[m // 2]) / 2.0

    # Median absolute deviation of recent efforts
    if m > 1:
        deviations = [abs(e - median_recent_effort) for e in recent_efforts]
        mad = sorted(deviations)[m // 2]
    else:
        mad = 0.0

    # Average absolute error over recent window
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Adaptive threshold: baseline + margin, but not too high
    # Use a small multiple of MAD to account for noise
    effort_threshold = baseline_effort + max(0.15, 2.0 * mad)

    # CUSUM-like accumulator for persistent error
    # We use a simple sum of recent errors (signed) to detect bias
    recent_errors = errors[-recent_n:]
    sum_recent_errors = sum(recent_errors)
    # Normalize by window length to get average signed error
    avg_recent_error = sum_recent_errors / len(recent_errors)

    # Anomaly detection:
    # 1. Effort significantly above baseline for at least 3 consecutive steps
    # 2. Or persistent positive error (tank level below setpoint) with elevated effort
    consecutive_high = 0
    for e in reversed(efforts):
        if e > effort_threshold:
            consecutive_high += 1
        else:
            break

    # Persistent error: average signed error > 0.05 and effort above baseline*1.05
    persistent_error = avg_recent_error > 0.05 and median_recent_effort > baseline_effort * 1.05

    # Very high effort with significant error
    very_high_effort = median_recent_effort > 2.5
    high_error = avg_recent_abs_error > 0.08

    anomaly_flag = (consecutive_high >= 3) or persistent_error or (very_high_effort and high_error)

    # Setpoint adjustment
    if anomaly_flag:
        # Estimate excess effort as the difference between recent median and baseline
        excess = max(0.0, median_recent_effort - baseline_effort)
        # Adjustment proportional to excess, with a minimum step to ensure compensation
        # Use a gain that is larger for severe leaks
        if median_recent_effort > 3.0:
            adjustment = min(1.5, max(0.5, excess * 0.8))
        else:
            adjustment = min(1.0, max(0.3, excess * 0.5))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {median_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif (median_recent_effort < baseline_effort * 1.02 and
          avg_recent_abs_error < 0.05 and
          active_setpoint < nominal_target):
        # Stable and below nominal: restore gradually
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.3)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }