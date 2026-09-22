def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.', 'adjusted_setpoint': float(active_setpoint), 'anomaly_flag': False}

    efforts = []
    errors = []
    for step in telemetry_window:
        eff = 0.0
        err = 0.0
        if isinstance(step, dict):
            try:
                eff = float(step.get('pump_effort', 0.0))
            except (TypeError, ValueError):
                eff = 0.0
            try:
                err = float(step.get('error', 0.0))
            except (TypeError, ValueError):
                err = 0.0
        efforts.append(eff)
        errors.append(err)

    def med(vals):
        if not vals:
            return 0.0
        sv = sorted(vals)
        m = len(sv)
        if m % 2 == 1:
            return sv[m // 2]
        return 0.5 * (sv[m // 2 - 1] + sv[m // 2])

    def avg(vals):
        if not vals:
            return 0.0
        return sum(vals) / float(len(vals))

    def linear_slope(vals):
        m = len(vals)
        if m < 2:
            return 0.0
        mx = (m - 1) / 2.0
        my = avg(vals)
        cov = 0.0
        var = 0.0
        for i in range(m):
            dx = i - mx
            cov += dx * (vals[i] - my)
            var += dx * dx
        if var <= 1e-9:
            return 0.0
        return cov / var

    short_n = min(5, n)
    mid_n = min(10, n)
    long_n = min(20, n)

    e_short = efforts[-short_n:]
    e_mid = efforts[-mid_n:]
    e_long = efforts[-long_n:]
    r_short = errors[-short_n:]

    med_effort_short = med(e_short)
    med_error_short = med(r_short)
    err_slope = linear_slope(r_short)

    # expected effort for the current operating point (hydraulic sqrt model)
    if nominal_target and nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        ratio = max(0.1, min(1.5, ratio))
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    # only a small allowance for a genuine steady-state offset: a PI level
    # loop removes it, so it must never be used to cancel a real leak.
    expected_effort = base_effort + 0.4 * max(0.0, -med_error_short)

    dev_short = [x - expected_effort for x in e_short]
    dev_mid = [x - expected_effort for x in e_mid]
    dev_long = [x - expected_effort for x in e_long]

    med_dev_short = med(dev_short)
    med_dev_mid = med(dev_mid)
    med_dev_long = med(dev_long)
    max_dev_short = max(dev_short) if dev_short else 0.0

    mad = med([abs(x - med_effort_short) for x in e_short])
    sigma = max(0.02, 1.4826 * mad)

    t_mild = max(0.09, 3.0 * sigma)
    t_mod = max(0.18, 4.5 * sigma)
    t_sev = max(0.45, 7.0 * sigma)

    frac_short = sum(1 for d in dev_short if d > 0.7 * t_mild) / float(short_n)
    frac_mid = sum(1 for d in dev_mid if d > 0.7 * t_mild) / float(mid_n)
    frac_long = sum(1 for d in dev_long if d > 0.7 * t_mild) / float(long_n)

    # the loop is actively refilling the tank (level below target and rising):
    # high effort is expected and must not be reported as a leak
    refilling = (med_error_short < -0.04 and err_slope > 0.02)

    sustained = (short_n >= 4 and long_n >= 6 and
                 med_dev_short > t_mild and
                 med_dev_long > 0.6 * t_mild and
                 frac_short >= 0.6 and
                 frac_long >= 0.5)

    persistent = (long_n >= 8 and
                  med_dev_long > t_mod and
                  med_dev_short > 0.6 * t_mod and
                  frac_mid >= 0.6 and
                  frac_long >= 0.6)

    acute = (short_n >= 3 and
             med_dev_short > 0.8 * t_mod and
             max_dev_short > t_sev)

    sudden = (short_n >= 3 and
              med_dev_short > t_mod and
              frac_short >= 0.6)

    anomaly_flag = bool(sustained or persistent or acute or sudden)

    # fast clear: a clean recent window overrides the slow long-horizon detectors
    if short_n >= 3 and med_dev_short < 0.4 * t_mild and frac_short <= 0.2:
        anomaly_flag = False

    # refilling context cancels ordinary evidence but never a saturating fault
    if refilling and med_dev_short <= 1.0 and max_dev_short <= 1.4:
        anomaly_flag = False

    if anomaly_flag:
        if med_dev_short > 0.6:
            drop = 0.20
        elif med_dev_short > 0.3:
            drop = 0.12
        else:
            drop = 0.06
        floor = 0.0
        if nominal_target and nominal_target > 0.0:
            floor = 0.4 * nominal_target
        adjusted_setpoint = active_setpoint - drop
        if adjusted_setpoint < floor:
            adjusted_setpoint = floor
        if adjusted_setpoint < 0.0:
            adjusted_setpoint = 0.0
        adjusted_setpoint = float(adjusted_setpoint)
        diagnosis = ('Anomaly detected: pump effort median %.3f vs expected %.3f (excess %.3f); lowering setpoint by %.2f.'
                     % (med_effort_short, expected_effort, med_dev_short, drop))
    else:
        if nominal_target and active_setpoint < nominal_target - 1e-9:
            gap = nominal_target - active_setpoint
            step_up = 0.3 * gap
            if step_up < 0.10:
                step_up = 0.10
            if step_up > 0.25:
                step_up = 0.25
            if step_up > gap:
                step_up = gap
            adjusted_setpoint = active_setpoint + step_up
            if adjusted_setpoint > nominal_target:
                adjusted_setpoint = nominal_target
            adjusted_setpoint = float(adjusted_setpoint)
            diagnosis = ('Nominal operation; restoring setpoint toward nominal (%.3f -> %.3f).'
                         % (active_setpoint, adjusted_setpoint))
        else:
            adjusted_setpoint = float(active_setpoint)
            diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': adjusted_setpoint,
        'anomaly_flag': anomaly_flag,
    }
