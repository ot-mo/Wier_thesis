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

    # Nominal effort estimate: effort needed to maintain setpoint under no leak
    nominal_effort = 0.5 * nominal_target + 0.1

    # Robust baseline: median of first up to 20 samples
    baseline_n = min(20, n)
    baseline_effort = sorted(efforts[:baseline_n])[baseline_n // 2]

    # Recent effort: median of last 5 steps (or all if fewer)
    recent_efforts = efforts[-5:] if n >= 5 else efforts
    recent_efforts_sorted = sorted(recent_efforts)
    m = len(recent_efforts_sorted)
    if m % 2 == 1:
        median_recent_effort = recent_efforts_sorted[m // 2]
    else:
        median_recent_effort = (recent_efforts_sorted[m // 2 - 1] + recent_efforts_sorted[m // 2]) / 2.0

    # Recent error: average absolute error of last 5 steps
    recent_abs_errors = abs_errors[-5:] if n >= 5 else abs_errors
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Dynamic threshold: max of nominal-based, baseline-based, and absolute floor
    # Use a multiplier that is not too high to catch leaks early
    threshold_nominal = nominal_effort * 1.5
    threshold_baseline = baseline_effort * 1.2 + 0.1
    effort_threshold = max(threshold_nominal, threshold_baseline, 0.5)

    # Anomaly detection with confirmation: require 3 consecutive samples above threshold
    # We'll use a simple counter based on the last few efforts
    consecutive_high = 0
    for e in reversed(efforts):
        if e > effort_threshold:
            consecutive_high += 1
        else:
            break
    anomaly_flag = consecutive_high >= 3

    # Also flag if recent effort is very high and error is significant, but with hysteresis
    very_high_effort = median_recent_effort > effort_threshold * 1.2
    high_error = avg_recent_abs_error > 0.15
    if very_high_effort and high_error:
        anomaly_flag = True

    # Setpoint adjustment
    if anomaly_flag:
        excess = max(0.0, median_recent_effort - nominal_effort)
        adjustment = min(0.5, max(0.1, excess * 0.3))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {median_recent_effort:.2f} vs threshold {effort_threshold:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_recent_effort < effort_threshold * 0.9 and avg_recent_abs_error < 0.1 and active_setpoint < nominal_target:
        # Stable and below nominal: restore gradually
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.2)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }