def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.',
                'adjusted_setpoint': float(active_setpoint),
                'anomaly_flag': False}

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

    def lin_slope(vals):
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
    err_slope = lin_slope(r_short)

    try:
        sp = float(active_setpoint)
    except (TypeError, ValueError):
        sp = 0.0
    try:
        nt = float(nominal_target)
    except (TypeError, ValueError):
        nt = 0.0

    if nt > 0.0:
        ratio = sp / nt
        if ratio < 0.1:
            ratio = 0.1
        elif ratio > 1.5:
            ratio = 1.5
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    # tightly bounded allowance for a genuine level deficit: a PI level loop
    # removes steady offset, so the level error must never be able to cancel
    # a real (leak driven) effort excess
    allowance = 0.5 * max(0.0, -med_error_short)
    if allowance > 0.10:
        allowance = 0.10
    expected_effort = base_effort + allowance

    dev_short = [x - expected_effort for x in e_short]
    dev_mid = [x - expected_effort for x in e_mid]
    dev_long = [x - expected_effort for x in e_long]

    med_dev_short = med(dev_short)
    med_dev_mid = med(dev_mid)
    med_dev_long = med(dev_long)
    max_dev_short = max(dev_short) if dev_short else 0.0

    mad = med([abs(x - med_effort_short) for x in e_short])
    sigma = 1.4826 * mad
    if sigma < 0.035:
        sigma = 0.035

    t_entry = 3.5 * sigma
    if t_entry < 0.18:
        t_entry = 0.18
    t_mod = 6.0 * sigma
    if t_mod < 0.34:
        t_mod = 0.34
    t_sev = 10.0 * sigma
    if t_sev < 0.70:
        t_sev = 0.70

    frac_short = 0.0
    if short_n > 0:
        frac_short = sum(1 for d in dev_short if d > 0.6 * t_entry) / float(short_n)
    frac_mid = 0.0
    if mid_n > 0:
        frac_mid = sum(1 for d in dev_mid if d > 0.6 * t_entry) / float(mid_n)
    frac_long = 0.0
    if long_n > 0:
        frac_long = sum(1 for d in dev_long if d > 0.5 * t_entry) / float(long_n)

    # a high effort is legitimate while the loop is genuinely refilling a
    # level that sits below target and is rising; only a saturating fault
    # escapes this guard
    refilling = (med_error_short < -0.10 and err_slope > 0.03)

    severe = (short_n >= 3 and
              max_dev_short > t_sev and
              med_dev_short > 0.6 * t_sev)

    sustained = (short_n >= 4 and mid_n >= 6 and long_n >= 8 and
                 med_dev_short > t_entry and
                 med_dev_mid > 0.6 * t_entry and
                 med_dev_long > 0.5 * t_entry and
                 frac_short >= 0.6 and
                 frac_mid >= 0.5 and
                 frac_long >= 0.5)

    persistent = (long_n >= 8 and mid_n >= 6 and
                  med_dev_long > t_mod and
                  med_dev_short > 0.6 * t_mod and
                  frac_mid >= 0.6 and
                  frac_long >= 0.6)

    anomaly_flag = bool(severe or sustained or persistent)

    # hysteresis clearing: the effort has returned to, or is clearly decaying
    # back to, the model expectation, so stale long-horizon evidence must be
    # discarded (this is what lets the setpoint be restored after a fault)
    clear_level = 0.5 * t_entry
    if short_n >= 3:
        recent = dev_short[-3:]
        if max(recent) <= clear_level:
            anomaly_flag = False
    if anomaly_flag and short_n >= 4:
        if med_dev_short <= clear_level and frac_short <= 0.25:
            anomaly_flag = False
        elif dev_short[-1] <= 0.5 * t_mod and lin_slope(dev_short) <= -0.05:
            anomaly_flag = False

    if anomaly_flag and refilling and max_dev_short < 1.6 and med_dev_short < 1.2:
        anomaly_flag = False

    if anomaly_flag:
        if med_dev_short > 0.6 * t_mod:
            drop = 0.15
        elif med_dev_short > 0.3 * t_mod:
            drop = 0.10
        else:
            drop = 0.06
        floor = 0.0
        if nt > 0.0:
            floor = 0.4 * nt
        adjusted_setpoint = sp - drop
        if adjusted_setpoint < floor:
            adjusted_setpoint = floor
        if adjusted_setpoint < 0.0:
            adjusted_setpoint = 0.0
        adjusted_setpoint = float(adjusted_setpoint)
        diagnosis = ('Anomaly detected: pump effort median %.3f vs expected %.3f '
                     '(excess %.3f); lowering setpoint by %.2f.'
                     % (med_effort_short, expected_effort, med_dev_short, drop))
    else:
        if nt > 0.0 and sp < nt - 1e-9:
            gap = nt - sp
            step_up = 0.25 * gap
            if step_up < 0.08:
                step_up = 0.08
            if step_up > 0.22:
                step_up = 0.22
            if step_up > gap:
                step_up = gap
            adjusted_setpoint = sp + step_up
            if adjusted_setpoint > nt:
                adjusted_setpoint = nt
            adjusted_setpoint = float(adjusted_setpoint)
            diagnosis = ('Nominal operation; restoring setpoint toward nominal (%.3f -> %.3f).'
                         % (sp, adjusted_setpoint))
        else:
            adjusted_setpoint = float(sp)
            diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': adjusted_setpoint,
        'anomaly_flag': anomaly_flag,
    }