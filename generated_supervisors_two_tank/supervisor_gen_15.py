def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_sp = float(active_setpoints['tank1'])
    tank2_sp = float(active_setpoints['tank2'])
    nom1 = float(nominal_targets['tank1'])
    nom2 = float(nominal_targets['tank2'])

    n = len(telemetry_window)
    if n == 0:
        return {
            'diagnosis': 'no telemetry available',
            'adjusted_setpoints': {'tank1': tank1_sp, 'tank2': tank2_sp},
            'anomaly_flags': {'tank1': False, 'tank2': False},
        }

    def col(tank, key):
        out = []
        for step in telemetry_window:
            v = 0.0
            if isinstance(step, dict):
                d = step.get(tank)
                if isinstance(d, dict):
                    x = d.get(key, 0.0)
                    if isinstance(x, bool):
                        x = 0.0
                    if isinstance(x, (int, float)):
                        v = float(x)
            out.append(v)
        return out

    eff1 = col('tank1', 'pump_effort')
    eff2 = col('tank2', 'pump_effort')
    err1 = col('tank1', 'error')
    err2 = col('tank2', 'error')
    lev1 = col('tank1', 'level')
    lev2 = col('tank2', 'level')

    def mean(xs):
        if not xs:
            return 0.0
        return sum(xs) / float(len(xs))

    def mean_abs(xs):
        if not xs:
            return 0.0
        total = 0.0
        for x in xs:
            total += abs(x)
        return total / float(len(xs))

    def noise_sigma(xs):
        if len(xs) < 3:
            return 0.0
        diffs = []
        for i in range(1, len(xs)):
            diffs.append(xs[i] - xs[i - 1])
        mu = mean(diffs)
        acc = 0.0
        for d in diffs:
            acc += (d - mu) * (d - mu)
        denom = float(len(diffs) - 1)
        if denom <= 0.0:
            return 0.0
        return ((acc / denom) ** 0.5) / 1.4142135623730951

    m = min(6, n)
    q = max(2, min(4, n // 3))
    b = max(2, min(4, n // 3))

    def baseline(effs):
        if len(effs) <= b:
            return mean(effs)
        best = None
        for s in range(0, len(effs) - b + 1):
            v = mean(effs[s:s + b])
            if best is None or v < best:
                best = v
        return best

    def smoothed(xs):
        out = []
        for i in range(len(xs)):
            lo = i - 1
            if lo < 0:
                lo = 0
            hi = i + 2
            if hi > len(xs):
                hi = len(xs)
            out.append(mean(xs[lo:hi]))
        return out

    def onset(effs):
        sm = smoothed(effs)
        base = baseline(effs)
        delta = max(0.30, 0.22 * base)
        for i in range(len(sm)):
            if sm[i] > base + delta:
                return i
        return None

    def recovery(effs, errs, levs, nominal):
        rec = False
        if n >= 2 * q:
            dev_prior = 0.0
            dev_recent = 0.0
            for i in range(n - 2 * q, n - q):
                dev_prior += abs(levs[i] - nominal)
            for i in range(n - q, n):
                dev_recent += abs(levs[i] - nominal)
            dev_prior = dev_prior / float(q)
            dev_recent = dev_recent / float(q)
            if dev_prior > 0.05 and dev_recent < 0.85 * dev_prior:
                rec = True
            if not rec:
                prior_err = mean_abs(errs[-2 * q:-q])
                recent_err = mean_abs(errs[-q:])
                prior_eff = mean(effs[-2 * q:-q])
                recent_eff = mean(effs[-q:])
                if prior_err > 0.05 and recent_err < 0.80 * prior_err and recent_eff < 0.93 * prior_eff:
                    rec = True
        return rec

    te1 = mean(eff1[-m:])
    te2 = mean(eff2[-m:])
    ae1 = mean_abs(err1[-m:])
    ae2 = mean_abs(err2[-m:])
    be1 = mean(err1[-m:])
    be2 = mean(err2[-m:])
    base1 = baseline(eff1)
    base2 = baseline(eff2)
    rise1 = te1 - base1
    rise2 = te2 - base2
    sig1 = noise_sigma(eff1)
    sig2 = noise_sigma(eff2)
    rec1 = recovery(eff1, err1, lev1, nom1)
    rec2 = recovery(eff2, err2, lev2, nom2)
    on1 = onset(eff1)
    on2 = onset(eff2)

    ref1 = base1 if base1 > 0.5 else 0.5
    ref2 = base2 if base2 > 0.5 else 0.5

    floor1 = 0.38
    if 2.5 * sig1 > floor1:
        floor1 = 2.5 * sig1
    floor2 = 0.34
    if 2.5 * sig2 > floor2:
        floor2 = 2.5 * sig2

    t1_strong = te1 > 3.4
    t1_abs = (te1 > 2.45) and (ae1 > 0.22)
    t1_errdom = (ae1 > 0.30) and (abs(be1) > 0.22)
    t1_rel = (rise1 > floor1) and (rise1 > 0.24 * ref1) and ((ae1 > 0.15) or (abs(be1) > 0.17))
    raw1 = t1_strong or t1_abs or t1_errdom or t1_rel
    tank1_anom = bool(raw1 and not rec1)

    t2_strong = (te2 > 2.9) and (ae2 > 0.32)
    t2_abs = (te2 > 2.0) and (ae2 > 0.20)
    t2_errdom = (ae2 > 0.26) and (abs(be2) > 0.20)
    t2_rel = (rise2 > floor2) and (rise2 > 0.21 * ref2) and ((ae2 > 0.14) or (abs(be2) > 0.16))
    raw2 = t2_strong or t2_abs or t2_errdom or t2_rel

    independent2 = bool(
        raw2
        and (te2 > 2.5)
        and (ae2 > 0.27)
        and (on2 is not None)
        and (on1 is None or on2 <= on1 + 1)
    )
    cascade = bool(tank1_anom and raw2 and (not t2_strong) and (not independent2))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        new1 = max(nom1 - MAX_DEPRESS, tank1_sp - LOWER_STEP)
        diag1 = 'tank1 anomaly: effort/error above its own robust baseline'
    elif tank1_sp < nom1:
        new1 = min(nom1, tank1_sp + RESTORE_STEP)
        diag1 = 'tank1 healthy: restoring setpoint toward nominal'
    else:
        new1 = tank1_sp
        diag1 = 'tank1 nominal'

    if tank2_anom:
        new2 = max(nom2 - MAX_DEPRESS, tank2_sp - LOWER_STEP)
        diag2 = 'tank2 anomaly: independent evidence beyond upstream coupling'
    elif cascade:
        new2 = tank2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault'
    elif tank2_sp < nom2:
        new2 = min(nom2, tank2_sp + RESTORE_STEP)
        diag2 = 'tank2 healthy: restoring setpoint toward nominal'
    else:
        new2 = tank2_sp
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }
