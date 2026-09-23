def supervise(telemetry_window, active_setpoints, nominal_targets):
    def _num(src, key, default):
        try:
            if isinstance(src, dict):
                v = src.get(key, default)
            else:
                return float(default)
            if isinstance(v, bool):
                return float(default)
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    t1_sp = _num(active_setpoints, 'tank1', 0.0)
    t2_sp = _num(active_setpoints, 'tank2', 0.0)
    nom1 = _num(nominal_targets, 'tank1', t1_sp)
    nom2 = _num(nominal_targets, 'tank2', t2_sp)

    n = len(telemetry_window) if isinstance(telemetry_window, (list, tuple)) else 0
    if n == 0:
        return {
            'diagnosis': 'no telemetry available',
            'adjusted_setpoints': {'tank1': t1_sp, 'tank2': t2_sp},
            'anomaly_flags': {'tank1': False, 'tank2': False},
        }

    def series(tank, key):
        out = []
        for step in telemetry_window:
            v = 0.0
            if isinstance(step, dict):
                d = step.get(tank)
                if isinstance(d, dict):
                    x = d.get(key, 0.0)
                    if isinstance(x, bool):
                        x = 0.0
                    elif isinstance(x, (int, float)):
                        v = float(x)
            out.append(v)
        return out

    eff1 = series('tank1', 'pump_effort')
    eff2 = series('tank2', 'pump_effort')
    err1 = series('tank1', 'error')
    err2 = series('tank2', 'error')

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

    def quantile(xs, q):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m == 1:
            return s[0]
        if q <= 0.0:
            return s[0]
        if q >= 1.0:
            return s[-1]
        pos = q * (m - 1)
        lo = int(pos)
        hi = lo + 1
        if hi >= m:
            return s[-1]
        frac = pos - lo
        return s[lo] * (1.0 - frac) + s[hi] * frac

    def tail(xs, kk):
        if kk <= 0:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_rel(effs, errs, ref, eff_margin, err_margin, win):
        seg_e = tail(effs, win)
        seg_r = tail(errs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        bar = ref + eff_margin
        cnt = 0
        for i in range(m):
            if seg_e[i] > bar and abs(seg_r[i]) > err_margin:
                cnt += 1
        return cnt / float(m)

    def frac_abs(effs, errs, eff_bar, err_bar, win):
        seg_e = tail(effs, win)
        seg_r = tail(errs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if seg_e[i] > eff_bar and abs(seg_r[i]) > err_bar:
                cnt += 1
        return cnt / float(m)

    k = min(8, n)
    w = min(12, n)
    wlong = min(20, n)
    h = max(3, k // 2)
    if h > k:
        h = k

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    ref1 = quantile(eff1, 0.15)
    ref2 = quantile(eff2, 0.15)

    dev1 = te1 - ref1
    raw_dev2 = te2 - ref2

    abs1_high = (te1 > 2.2) and (ae1 > 0.14)
    dev1_high = (dev1 > 0.30) and (ae1 > 0.12)
    sust_rel1 = frac_rel(eff1, err1, ref1, 0.25, 0.10, w) >= 0.6
    sust_abs1 = frac_abs(eff1, err1, 2.0, 0.14, wlong) >= 0.6
    raw1 = abs1_high or dev1_high or sust_rel1 or sust_abs1

    upstream1 = raw1 or (te1 > 2.0) or (dev1 > 0.25)
    cascade_gain = 0.70
    if upstream1:
        dev2 = raw_dev2 - cascade_gain * dev1
    else:
        dev2 = raw_dev2

    abs2_high = (te2 > 1.8) and (ae2 > 0.16)
    if upstream1:
        abs2_high = abs2_high and (dev2 > 0.08)
    dev2_high = (dev2 > 0.22) and (ae2 > 0.12)
    sust_rel2 = frac_rel(eff2, err2, ref2, 0.20, 0.10, w) >= 0.6
    if upstream1:
        sust_rel2 = sust_rel2 and (dev2 > 0.08)
    sust_abs2 = frac_abs(eff2, err2, 1.6, 0.14, wlong) >= 0.6
    if upstream1:
        sust_abs2 = sust_abs2 and (dev2 > 0.08)

    raw2 = abs2_high or dev2_high or sust_rel2 or sust_abs2

    sust_abs2_strong = frac_abs(eff2, err2, 2.0, 0.20, wlong) >= 0.6
    cascade = bool(upstream1 and raw2 and (dev2 < 0.10) and (te2 < 2.2) and (not sust_abs2_strong))

    if n >= 4:
        rec_eff1 = mean(tail(eff1, h))
        rec_err1 = mean_abs(tail(err1, h))
        rec_eff2 = mean(tail(eff2, h))
        rec_err2 = mean_abs(tail(err2, h))
        rec1 = rec_eff1 <= 2.1 and rec_err1 <= 0.12
        rec2 = rec_eff2 <= 1.7 and rec_err2 <= 0.12
    else:
        rec1 = False
        rec2 = False

    tank1_anom = bool(raw1 and not rec1)
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort/error above healthy reference)'
    elif (dev1 < 0.30) and (ae1 < 0.12) and (t1_sp < nom1):
        new1 = min(nom1, t1_sp + RESTORE_STEP)
        diag1 = 'tank1 stable, restoring toward nominal'
    else:
        new1 = t1_sp
        diag1 = 'tank1 nominal'

    if tank2_anom:
        cand2 = max(nom2 - MAX_DEPRESS, t2_sp - LOWER_STEP)
        if cand2 > t2_sp:
            cand2 = t2_sp
        new2 = cand2
        diag2 = 'tank2 anomaly suspected (independent, sustained evidence)'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif upstream1 and (te2 > 1.6 or ae2 > 0.12):
        new2 = t2_sp
        diag2 = 'tank2 perturbed but not independently flagged (upstream cascade)'
    elif (dev2 < 0.30) and (ae2 < 0.12) and (t2_sp < nom2):
        new2 = min(nom2, t2_sp + RESTORE_STEP)
        diag2 = 'tank2 stable, restoring toward nominal'
    else:
        new2 = t2_sp
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }