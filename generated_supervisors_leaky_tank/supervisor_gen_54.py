def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    try:
        sp = float(active_setpoint)
    except (TypeError, ValueError):
        sp = 0.0
    try:
        nt = float(nominal_target)
    except (TypeError, ValueError):
        nt = 0.0
    if sp != sp:
        sp = 0.0
    if nt != nt:
        nt = 0.0

    if n == 0:
        return {'diagnosis': 'No telemetry data.',
                'adjusted_setpoint': sp,
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
        elif isinstance(step, (list, tuple)):
            if len(step) > 0:
                try:
                    eff = float(step[0])
                except (TypeError, ValueError):
                    eff = 0.0
            if len(step) > 1:
                try:
                    err = float(step[1])
                except (TypeError, ValueError):
                    err = 0.0
        if not (eff > -1e12 and eff < 1e12):
            eff = 0.0
        if not (err > -1e12 and err < 1e12):
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

    def slope(vals):
        m = len(vals)
        if m < 3:
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

    short_n = min(4, n)
    mid_n = min(10, n)
    long_n = min(20, n)

    e_short = efforts[-short_n:]
    e_mid = efforts[-mid_n:]
    e_long = efforts[-long_n:]
    r_short = errors[-short_n:]
    r_mid = errors[-mid_n:]

    med_eff_short = med(e_short)
    med_eff_mid = med(e_mid)
    med_eff_long = med(e_long)
    med_err_short = med(r_short)
    med_err_mid = med(r_mid)
    last_eff = efforts[-1]
    prev_eff = efforts[-2] if n >= 2 else last_eff

    err_slope = slope(r_mid)

    # expected pump effort for the current operating point (hydraulic sqrt model)
    if nt > 0.0:
        ratio = sp / nt
        if ratio < 0.1:
            ratio = 0.1
        elif ratio > 1.5:
            ratio = 1.5
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    # BOUNDED allowance for a real, correctable level deficit: a PI level loop
    # removes steady offset, so a large deficit must never hide a leak.
    deficit = -med_err_short
    if deficit < 0.0:
        deficit = 0.0
    allowance = 0.4 * deficit
    if allowance > 0.45:
        allowance = 0.45
    expected = base_effort + allowance

    dev_short = med_eff_short - expected
    dev_mid = med_eff_mid - expected
    dev_long = med_eff_long - expected
    dev_now = last_eff - expected
    dev_prev = prev_eff - expected

    mad = med([abs(x - med_eff_mid) for x in e_mid])
    sigma = 1.4826 * mad
    if sigma < 0.015:
        sigma = 0.015

    t_mild = 3.0 * sigma
    if t_mild < 0.11:
        t_mild = 0.11
    t_mod = 4.5 * sigma
    if t_mod < 0.22:
        t_mod = 0.22
    t_sev = 7.0 * sigma
    if t_sev < 0.55:
        t_sev = 0.55

    band = 0.6 * t_mild
    frac_short = sum(1 for x in e_short if x - expected > band) / float(short_n)
    frac_mid = sum(1 for x in e_mid if x - expected > band) / float(mid_n)
    frac_long = sum(1 for x in e_long if x - expected > band) / float(long_n)

    # sustained moderate excess confirmed across all horizons
    sustained = (n >= 6 and
                 dev_short > t_mild and
                 dev_mid > 0.8 * t_mild and
                 dev_long > 0.6 * t_mild and
                 frac_mid >= 0.6 and
                 frac_long >= 0.5)

    # strong excess present in the recent and the long view
    persistent = (n >= 8 and
                  dev_short > 0.8 * t_mod and
                  dev_mid > 0.7 * t_mod and
                  dev_long > 0.6 * t_mod and
                  frac_mid >= 0.6 and
                  frac_long >= 0.6)

    # sharp onset: median plus the two most recent samples all clearly high
    acute = (short_n >= 3 and
             dev_short > t_mod and
             dev_now > 0.8 * t_mod and
             dev_prev > 0.8 * t_mod and
             frac_short >= 0.7)

    anomaly = bool(sustained or persistent or acute)

    # --- benign transient suppressor -------------------------------------
    # a refill/transient shows a clearly shrinking level error and only a
    # modest effort excess; a real leak keeps a standing excess.
    if anomaly:
        mag_short = abs(med_err_short)
        mag_mid = abs(med_err_mid)
        converging = (mag_mid > 0.01 and
                      mag_short < 0.7 * mag_mid and
                      err_slope > 0.0)
        modest = (med_eff_short < base_effort + 0.60 and
                  dev_short < 0.5 * t_sev)
        if converging and modest:
            anomaly = False

    # --- fast clear: effort is back at the expected level -----------------
    if anomaly:
        if dev_now < 0.5 * t_mild and dev_prev < 0.5 * t_mild:
            anomaly = False
        elif dev_short < 0.4 * t_mild and frac_short <= 0.25:
            anomaly = False

    if anomaly:
        if dev_short > 0.8 * t_sev or dev_short > 1.0:
            drop = 0.15
        elif dev_short > 0.4 * t_sev or dev_short > 0.5:
            drop = 0.10
        else:
            drop = 0.05
        floor = 0.0
        if nt > 0.0:
            floor = 0.5 * nt
        adjusted = sp - drop
        if adjusted < floor:
            adjusted = floor
        if adjusted < 0.0:
            adjusted = 0.0
        diagnosis = ('Anomaly detected: pump effort %.3f vs expected %.3f '
                     '(excess %.3f, sigma %.3f); reducing setpoint by %.2f.'
                     % (med_eff_short, expected, dev_short, sigma, drop))
    else:
        if nt > 0.0 and sp < nt - 1e-9:
            gap = nt - sp
            step_up = 0.35 * gap
            if step_up < 0.10:
                step_up = 0.10
            if step_up > 0.30:
                step_up = 0.30
            if step_up > gap:
                step_up = gap
            adjusted = sp + step_up
            if adjusted > nt:
                adjusted = nt
            diagnosis = ('Nominal operation; restoring setpoint toward nominal '
                         '(%.3f -> %.3f).' % (sp, adjusted))
        else:
            adjusted = sp
            diagnosis = 'Nominal operation, no action.'

    return {'diagnosis': diagnosis,
            'adjusted_setpoint': float(adjusted),
            'anomaly_flag': bool(anomaly)}
