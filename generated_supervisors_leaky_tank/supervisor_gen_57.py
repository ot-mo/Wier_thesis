def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)

    try:
        setpoint = float(active_setpoint)
    except (TypeError, ValueError):
        setpoint = 0.0
    try:
        nominal = float(nominal_target)
    except (TypeError, ValueError):
        nominal = 0.0

    if n == 0:
        return {'diagnosis': 'No telemetry data.',
                'adjusted_setpoint': float(setpoint),
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
        m = len(vals)
        if m == 0:
            return 0.0
        s = sorted(vals)
        if m % 2 == 1:
            return s[m // 2]
        return 0.5 * (s[m // 2 - 1] + s[m // 2])

    def slope(vals):
        m = len(vals)
        if m < 2:
            return 0.0
        mx = (m - 1) / 2.0
        my = sum(vals) / float(m)
        cov = 0.0
        var = 0.0
        for i in range(m):
            dx = i - mx
            cov += dx * (vals[i] - my)
            var += dx * dx
        if var < 1e-9:
            return 0.0
        return cov / var

    # expected effort for a fault free plant at this operating point
    if nominal > 0.0:
        ratio = setpoint / nominal
        if ratio < 0.1:
            ratio = 0.1
        elif ratio > 1.5:
            ratio = 1.5
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    # sign-agnostic level error context
    abs_err = [abs(x) for x in errors]
    k5 = 5 if n >= 5 else n
    ae_short = med(abs_err[-k5:])
    if n >= 10:
        ae_prev = med(abs_err[-10:-5])
    elif n > k5:
        ae_prev = med(abs_err[:-k5])
    else:
        ae_prev = ae_short
    converging = (ae_short < ae_prev - 0.008)

    allowance = 0.25 * max(0.0, ae_short - 0.05)
    if allowance > 0.08:
        allowance = 0.08
    expected = base_effort + allowance

    devs = [x - expected for x in efforts]
    d_last = devs[-1]
    d_short = devs[-5:] if n >= 5 else devs[:]
    d_mid = devs[-10:] if n >= 10 else devs[:]
    d_long = devs[-20:] if n >= 20 else devs[:]

    med_d_short = med(d_short)
    med_d_mid = med(d_mid)
    med_d_long = med(d_long)
    recent = devs[-3:] if n >= 3 else devs[:]
    med_recent = med(recent)

    med_last4 = med(devs[-4:]) if n >= 4 else med_recent
    if n >= 8:
        prev4 = med(devs[-8:-4])
    else:
        prev4 = med_last4
    decaying = (med_last4 < prev4 - 0.05)

    # robust noise scale from effort dispersion (drift-safe: uses MAD)
    w = efforts[-20:] if n > 20 else efforts
    med_w = med(w)
    mad = med([abs(x - med_w) for x in w])
    sigma = 1.4826 * mad
    if sigma < 0.03:
        sigma = 0.03

    t1 = max(0.14, 3.5 * sigma)
    t2 = max(0.30, 6.0 * sigma)
    t3 = max(0.60, 11.0 * sigma)

    frac_short = sum(1 for d in d_short if d > 0.6 * t1) / float(len(d_short))
    frac_mid = sum(1 for d in d_mid if d > 0.6 * t1) / float(len(d_mid))
    frac_long = sum(1 for d in d_long if d > 0.6 * t1) / float(len(d_long))
    recent_ok = (n < 3) or (med_recent > 0.35 * t1)

    # ---- layered evidence ----
    critical = (d_last > t3)
    if not critical and n >= 2 and med(devs[-2:]) > 0.85 * t3:
        critical = True
    if not critical and n >= 3 and med_recent > t3:
        critical = True

    strong = (n >= 3 and med_recent > t2 and d_last > 0.5 * t2 and
              frac_short >= 0.6)

    sustained = (n >= 6 and med_d_short > t1 and frac_short >= 0.8 and
                 med_d_mid > 0.75 * t1 and frac_mid >= 0.7 and recent_ok)

    persistent = (n >= 15 and med_d_long > 0.8 * t1 and frac_long >= 0.8 and
                  med_d_mid > 0.6 * t1 and recent_ok)

    # fast onset: effort climbing while the level error is not shrinking
    onset = (n >= 4 and slope(efforts[-4:]) > max(0.07, 2.5 * sigma) and
             d_last > 0.6 * t1 and med_recent > 0.5 * t1 and
             ae_short > ae_prev - 0.002)

    anomaly = bool(critical or strong or sustained or persistent or onset)

    # ---- transient suppression (start up fill / recovery / re-level) ----
    if anomaly and not critical and med_d_short < 1.0 * t2:
        if ae_short > 0.06 and converging:
            anomaly = False
        elif decaying and ae_short > 0.02 and med_d_short < 0.7 * t2:
            anomaly = False

    # ---- release the flag once the effort has really come back ----
    if anomaly and not critical:
        if (d_last < 0.35 * t1 and med_recent < 0.5 * t1 and
                (n < 4 or slope(efforts[-4:]) < 0.01)):
            anomaly = False

    # ---- actuation ----
    if anomaly:
        if med_d_short > 0.6:
            drop = 0.20
        elif med_d_short > 0.3:
            drop = 0.12
        else:
            drop = 0.07
        floor = 0.55 * nominal if nominal > 0.0 else 0.0
        adjusted_setpoint = setpoint - drop
        if adjusted_setpoint < floor:
            adjusted_setpoint = floor
        if adjusted_setpoint < 0.0:
            adjusted_setpoint = 0.0
        med_eff = med(efforts[-5:]) if n >= 5 else med(efforts)
        diagnosis = ('Anomaly detected: pump effort %.3f vs expected %.3f (excess %.3f); lowering setpoint %.3f -> %.3f.'
                     % (med_eff, expected, med_d_short, setpoint, adjusted_setpoint))
    else:
        if nominal > 0.0 and setpoint < nominal - 1e-9:
            gap = nominal - setpoint
            step_up = 0.45 * gap
            if step_up < 0.10:
                step_up = 0.10
            if step_up > 0.20:
                step_up = 0.20
            if step_up > gap:
                step_up = gap
            adjusted_setpoint = setpoint + step_up
            if adjusted_setpoint > nominal:
                adjusted_setpoint = nominal
            diagnosis = ('Nominal operation; restoring setpoint toward nominal (%.3f -> %.3f).'
                         % (setpoint, adjusted_setpoint))
        else:
            adjusted_setpoint = setpoint
            diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': float(adjusted_setpoint),
        'anomaly_flag': bool(anomaly),
    }
