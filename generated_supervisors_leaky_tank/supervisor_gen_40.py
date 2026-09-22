def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        try:
            e = float(step.get("pump_effort", 0.0))
        except (TypeError, ValueError):
            e = 0.0
        try:
            err = float(step.get("error", 0.0))
        except (TypeError, ValueError):
            err = 0.0
        efforts.append(e)
        errors.append(abs(err))

    # Use a baseline window for adaptive thresholds (last 20 or all)
    baseline_n = min(20, n)
    baseline_efforts = efforts[-baseline_n:]
    baseline_errors = errors[-baseline_n:]

    # Compute median and MAD for effort
    sorted_eff = sorted(baseline_efforts)
    m = len(sorted_eff)
    if m % 2 == 1:
        med_eff = sorted_eff[m // 2]
    else:
        med_eff = (sorted_eff[m // 2 - 1] + sorted_eff[m // 2]) / 2.0
    abs_dev_eff = [abs(x - med_eff) for x in baseline_efforts]
    sorted_dev_eff = sorted(abs_dev_eff)
    if m % 2 == 1:
        mad_eff = sorted_dev_eff[m // 2]
    else:
        mad_eff = (sorted_dev_eff[m // 2 - 1] + sorted_dev_eff[m // 2]) / 2.0
    # Avoid zero MAD
    mad_eff = max(mad_eff, 0.01)

    # Compute median and MAD for error
    sorted_err = sorted(baseline_errors)
    if m % 2 == 1:
        med_err = sorted_err[m // 2]
    else:
        med_err = (sorted_err[m // 2 - 1] + sorted_err[m // 2]) / 2.0
    abs_dev_err = [abs(x - med_err) for x in baseline_errors]
    sorted_dev_err = sorted(abs_dev_err)
    if m % 2 == 1:
        mad_err = sorted_dev_err[m // 2]
    else:
        mad_err = (sorted_dev_err[m // 2 - 1] + sorted_dev_err[m // 2]) / 2.0
    mad_err = max(mad_err, 0.005)

    # Dynamic thresholds: median + k*MAD, with minimum floors
    effort_threshold = max(med_eff + 3.0 * mad_eff, 1.2)
    error_threshold = max(med_err + 4.0 * mad_err, 0.05)

    # Check recent samples (last 3) for exceedances
    recent_n = min(3, n)
    recent_efforts = efforts[-recent_n:]
    recent_errors = errors[-recent_n:]
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_errors if e > error_threshold)
    # Require at least 2 of last 3 to exceed (or 1 if only 1 sample)
    required = max(1, int(0.6 * recent_n))
    anomaly_flag = (effort_exceed >= required) or (error_exceed >= required)

    # Compute median effort and average error over baseline for diagnosis
    avg_abs_error = sum(baseline_errors) / len(baseline_errors)

    if anomaly_flag:
        # Severity-scaled adjustment: larger decrement for higher effort
        severity = med_eff / max(nominal_target, 0.1)
        adjustment = max(0.05, min(0.5, 0.1 * severity))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {med_eff:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif med_eff < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
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