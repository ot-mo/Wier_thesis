def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.', 'adjusted_setpoint': active_setpoint, 'anomaly_flag': False}

    efforts = []
    signed_errors = []
    abs_errors = []
    for step in telemetry_window:
        e = step.get('pump_effort', 0.0)
        err = step.get('error', 0.0)
        efforts.append(e)
        signed_errors.append(err)
        abs_errors.append(abs(err))

    nominal = max(nominal_target, 1e-6)
    setpoint_ref = max(active_setpoint, 0.01 * nominal)

    short_n = min(3, n)
    mild_n = min(4, n)
    long_n = min(10, n)

    recent_efforts_short = efforts[-short_n:]
    recent_efforts_mild = efforts[-mild_n:]
    recent_efforts_long = efforts[-long_n:]
    recent_abs_errors_long = abs_errors[-long_n:]
    recent_signed_errors_long = signed_errors[-long_n:]

    def median(values):
        s = sorted(values)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    med_effort_long = median(recent_efforts_long)
    avg_abs_error_long = sum(recent_abs_errors_long) / len(recent_abs_errors_long) if recent_abs_errors_long else 0.0

    def linear_slope(y):
        if len(y) < 2:
            return 0.0
        x_mean = (len(y) - 1) / 2.0
        y_mean = sum(y) / len(y)
        sxy = sum((i - x_mean) * (y[i] - y_mean) for i in range(len(y)))
        sxx = sum((i - x_mean) ** 2 for i in range(len(y)))
        if sxx == 0:
            return 0.0
        return sxy / sxx

    error_slope = linear_slope(recent_signed_errors_long)

    high_effort_threshold = max(1.35 * setpoint_ref, 1.2 * nominal)
    low_effort_threshold = max(1.1 * setpoint_ref, 0.85 * nominal)
    restore_effort_threshold = max(1.05 * setpoint_ref, 0.85 * nominal)

    severe_condition = (len(recent_efforts_short) >= 3 and
                        all(e > high_effort_threshold for e in recent_efforts_short) and
                        error_slope > -0.01)

    mild_condition = (len(recent_efforts_mild) >= 4 and
                      all(e > low_effort_threshold for e in recent_efforts_mild) and
                      med_effort_long > low_effort_threshold and
                      error_slope > -0.01)

    anomaly_flag = severe_condition or mild_condition

    if anomaly_flag:
        adjustment = 0.2
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f'Anomaly detected: effort {med_effort_long:.2f}, error {avg_abs_error_long:.2f}, lowering setpoint by {adjustment:.2f}.'
    elif med_effort_long < restore_effort_threshold and avg_abs_error_long < 0.03 * nominal and active_setpoint < nominal_target:
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.1)
        diagnosis = 'System stable and below nominal, restoring setpoint toward nominal.'
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': adjusted_setpoint,
        'anomaly_flag': anomaly_flag,
    }