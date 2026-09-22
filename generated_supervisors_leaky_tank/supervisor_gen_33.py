def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    # Extract efforts and errors
    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(step.get("pump_effort", 0.0))
        errors.append(step.get("error", 0.0))
    abs_errors = [abs(e) for e in errors]

    # Use a window of up to 20 recent samples for robust statistics
    window_size = min(20, n)
    recent_efforts = efforts[-window_size:]
    recent_abs_errors = abs_errors[-window_size:]

    # Compute median and MAD for efforts
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0

    # MAD for efforts
    abs_dev_efforts = [abs(e - median_effort) for e in recent_efforts]
    sorted_dev_efforts = sorted(abs_dev_efforts)
    if m % 2 == 1:
        mad_effort = sorted_dev_efforts[m // 2]
    else:
        mad_effort = (sorted_dev_efforts[m // 2 - 1] + sorted_dev_efforts[m // 2]) / 2.0
    # Scale MAD to be consistent with standard deviation for normal distribution
    mad_effort_scaled = mad_effort * 1.4826
    if mad_effort_scaled < 1e-6:
        mad_effort_scaled = 1e-6

    # Compute median and MAD for abs errors
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
    mad_error_scaled = mad_error * 1.4826
    if mad_error_scaled < 1e-6:
        mad_error_scaled = 1e-6

    # Compute robust z-scores for the most recent sample
    last_effort = efforts[-1]
    last_abs_error = abs_errors[-1]
    z_effort = (last_effort - median_effort) / mad_effort_scaled
    z_error = (last_abs_error - median_error) / mad_error_scaled

    # Combined anomaly score: max of the two z-scores
    anomaly_score = max(z_effort, z_error)

    # Threshold for anomaly detection
    threshold = 3.5

    # Require persistence: check if the last two samples both exceed threshold
    # For simplicity, we check the last two samples' z-scores
    if n >= 2:
        prev_effort = efforts[-2]
        prev_abs_error = abs_errors[-2]
        z_effort_prev = (prev_effort - median_effort) / mad_effort_scaled
        z_error_prev = (prev_abs_error - median_error) / mad_error_scaled
        anomaly_score_prev = max(z_effort_prev, z_error_prev)
        persistent = (anomaly_score > threshold) and (anomaly_score_prev > threshold)
    else:
        persistent = anomaly_score > threshold

    anomaly_flag = persistent

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # Proportional adjustment based on anomaly score, capped
        adjustment = min(0.5, max(0.1, 0.1 * (anomaly_score - threshold)))
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