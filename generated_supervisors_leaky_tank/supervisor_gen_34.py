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

    # Use a baseline window from the earliest part of the telemetry (assumed fault-free)
    # If window is short, use the whole window as baseline.
    baseline_n = min(20, n)
    baseline_efforts = efforts[:baseline_n]
    baseline_abs_errors = abs_errors[:baseline_n]

    # Robust baseline: median of the lowest 50% of efforts (to avoid initial transient)
    sorted_base_efforts = sorted(baseline_efforts)
    half = max(1, len(sorted_base_efforts) // 2)
    low_efforts = sorted_base_efforts[:half]
    base_effort = sum(low_efforts) / len(low_efforts)
    if base_effort < 0.1:
        base_effort = 0.1  # avoid division by zero

    sorted_base_errors = sorted(baseline_abs_errors)
    low_errors = sorted_base_errors[:half]
    base_error = sum(low_errors) / len(low_errors)
    if base_error < 0.01:
        base_error = 0.01

    # Recent window for detection (last 5 samples)
    recent_n = min(5, n)
    recent_efforts = efforts[-recent_n:]
    recent_abs_errors = abs_errors[-recent_n:]

    # Compute median of recent efforts and errors
    def median(lst):
        s = sorted(lst)
        m = len(s)
        if m % 2 == 1:
            return s[m // 2]
        else:
            return (s[m // 2 - 1] + s[m // 2]) / 2.0

    med_effort = median(recent_efforts)
    med_error = median(recent_abs_errors)

    # Ratios relative to baseline
    effort_ratio = med_effort / base_effort
    error_ratio = med_error / base_error

    # Adaptive thresholds: require both effort and error to be elevated
    # Use hysteresis: high threshold to trigger, low threshold to clear
    # We don't have state, so we use a single threshold but with a margin.
    # To reduce false positives, require effort_ratio > 1.5 and error_ratio > 2.0
    # For mild leaks, effort_ratio may be ~1.2 and error_ratio ~1.5, so we need lower thresholds.
    # But noisy sensor can cause error_ratio to spike. Use a combined score.
    leak_score = max(0.0, (effort_ratio - 1.0)) * max(0.0, (error_ratio - 1.0))
    # Alternatively, use a weighted sum
    # leak_score = 0.7 * max(0, effort_ratio - 1.0) + 0.3 * max(0, error_ratio - 1.0)

    # Threshold for leak_score: tune to balance missed vs false positives
    # From scenarios: baseline_no_fault has effort_ratio ~1.0, error_ratio ~1.0 -> score ~0
    # standard_leak: effort ~1.8, error ~0.026 -> ratios ~1.8 and ~2.6 -> score ~0.8*1.6=1.28
    # late_mild_leak: effort ~1.245, error ~0.041 -> ratios ~1.245 and ~4.1 -> score ~0.245*3.1=0.76
    # noisy_sensor_no_fault: effort ~0.922, error ~0.02 -> ratios ~0.922 and ~2.0 -> score ~0 (effort below baseline)
    # So threshold around 0.5 should catch leaks and avoid false positives.
    leak_threshold = 0.5

    anomaly_flag = leak_score > leak_threshold

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # Proportional adjustment: larger leak -> larger reduction
        # Cap adjustment to avoid safety violations (e.g., max 0.5)
        adjustment = min(0.5, 0.2 * leak_score)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort ratio {effort_ratio:.2f}, error ratio {error_ratio:.2f}, leak score {leak_score:.2f}, lowering setpoint by {adjustment:.2f}."
    elif med_effort < 1.1 * base_effort and avg_abs_error < 1.5 * base_error and active_setpoint < nominal_target:
        # System stable and below nominal, restore setpoint toward nominal
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