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

    # Dual windows: short for fast detection, long for confirmation
    short_n = min(3, n)
    long_n = min(10, n)
    short_efforts = efforts[-short_n:]
    short_abs_errors = abs_errors[-short_n:]
    long_efforts = efforts[-long_n:]
    long_abs_errors = abs_errors[-long_n:]

    # Adaptive thresholds based on nominal target and recent variability
    # Effort threshold: nominal effort is around 1.0, allow up to 1.5x nominal
    effort_threshold = max(1.5, 1.2 * nominal_target)
    # Error threshold: base on nominal target and recent noise level
    # Use median absolute deviation of long window errors as noise estimate
    sorted_long_errors = sorted(long_abs_errors)
    m = len(sorted_long_errors)
    if m % 2 == 1:
        median_long_error = sorted_long_errors[m // 2]
    else:
        median_long_error = (sorted_long_errors[m // 2 - 1] + sorted_long_errors[m // 2]) / 2.0
    # MAD: median of absolute deviations from median
    deviations = [abs(e - median_long_error) for e in long_abs_errors]
    sorted_dev = sorted(deviations)
    if m % 2 == 1:
        mad = sorted_dev[m // 2]
    else:
        mad = (sorted_dev[m // 2 - 1] + sorted_dev[m // 2]) / 2.0
    # Error threshold: at least 0.05 or 3*MAD, but not too high
    error_threshold = max(0.05, min(0.2, 3.0 * mad))

    # Short window detection: any exceedance in short window triggers immediate flag
    short_effort_exceed = any(e > effort_threshold for e in short_efforts)
    short_error_exceed = any(e > error_threshold for e in short_abs_errors)

    # Long window confirmation: majority (>=60%) exceedance in long window
    long_effort_exceed = sum(1 for e in long_efforts if e > effort_threshold)
    long_error_exceed = sum(1 for e in long_abs_errors if e > error_threshold)
    required_long = max(1, int(0.6 * long_n))
    long_effort_persistent = long_effort_exceed >= required_long
    long_error_persistent = long_error_exceed >= required_long

    # Anomaly if short window shows severe exceedance OR long window shows persistence
    # Severe exceedance: effort > 2*threshold or error > 2*threshold
    severe_effort = any(e > 2.0 * effort_threshold for e in short_efforts)
    severe_error = any(e > 2.0 * error_threshold for e in short_abs_errors)
    anomaly_flag = severe_effort or severe_error or short_effort_exceed or short_error_exceed or long_effort_persistent or long_error_persistent

    # Compute median effort and average error for diagnosis and adjustment
    sorted_short_efforts = sorted(short_efforts)
    m_short = len(sorted_short_efforts)
    if m_short % 2 == 1:
        median_effort = sorted_short_efforts[m_short // 2]
    else:
        median_effort = (sorted_short_efforts[m_short // 2 - 1] + sorted_short_efforts[m_short // 2]) / 2.0
    avg_abs_error = sum(short_abs_errors) / len(short_abs_errors)

    if anomaly_flag:
        # Proportional adjustment: larger reduction for higher effort or error
        # Base reduction 0.1, scale up to 0.5 based on severity
        effort_ratio = median_effort / max(1.0, nominal_target)
        error_ratio = avg_abs_error / max(0.05, nominal_target)
        severity = max(effort_ratio, error_ratio)
        adjustment = min(0.5, max(0.1, 0.2 * severity))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
        # Recovery: increase setpoint gradually
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