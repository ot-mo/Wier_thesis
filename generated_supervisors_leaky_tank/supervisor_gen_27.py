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

    # Baseline: use the earliest 30% of the window (at least 3 samples) to estimate nominal behavior.
    baseline_n = max(3, int(0.3 * n))
    baseline_n = min(baseline_n, n)
    baseline_efforts = efforts[:baseline_n]
    baseline_abs_errors = abs_errors[:baseline_n]

    def median(vals):
        s = sorted(vals)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    baseline_effort = median(baseline_efforts)
    baseline_error = median(baseline_abs_errors)

    # Guard against zero baseline (e.g., no effort/error at start)
    if baseline_effort < 0.1:
        baseline_effort = 0.1
    if baseline_error < 0.01:
        baseline_error = 0.01

    # Recent windows
    short_n = min(3, n)
    long_n = min(10, n)
    recent_efforts_short = efforts[-short_n:]
    recent_abs_errors_short = abs_errors[-short_n:]
    recent_efforts_long = efforts[-long_n:]
    recent_abs_errors_long = abs_errors[-long_n:]

    recent_effort_short = median(recent_efforts_short)
    recent_error_short = median(recent_abs_errors_short)
    recent_effort_long = median(recent_efforts_long)
    recent_error_long = median(recent_abs_errors_long)

    # Relative thresholds
    effort_ratio_short = recent_effort_short / baseline_effort
    error_ratio_short = recent_error_short / baseline_error
    effort_ratio_long = recent_effort_long / baseline_effort
    error_ratio_long = recent_error_long / baseline_error

    # Persistence counts
    effort_exceed_short = sum(1 for e in recent_efforts_short if e > baseline_effort * 1.5)
    error_exceed_short = sum(1 for e in recent_abs_errors_short if e > baseline_error * 2.0)
    effort_exceed_long = sum(1 for e in recent_efforts_long if e > baseline_effort * 1.3)
    error_exceed_long = sum(1 for e in recent_abs_errors_long if e > baseline_error * 1.5)

    required_short = max(1, int(0.7 * short_n))
    required_long = max(1, int(0.6 * long_n))

    # Anomaly if both effort and error deviate significantly, with persistence
    anomaly_short = (effort_exceed_short >= required_short and error_exceed_short >= required_short)
    anomaly_long = (effort_exceed_long >= required_long and error_exceed_long >= required_long)
    anomaly_flag = anomaly_short or anomaly_long

    # Diagnosis and setpoint adjustment
    if anomaly_flag:
        adjustment = 0.3
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort ratio {effort_ratio_short:.2f}, error ratio {error_ratio_short:.2f}, lowering setpoint by {adjustment:.2f}."
    elif recent_effort_long < baseline_effort * 1.1 and recent_error_long < baseline_error * 1.2 and active_setpoint < nominal_target:
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