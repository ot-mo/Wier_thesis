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

    if not isinstance(telemetry_window, (list, tuple)) or len(telemetry_window) == 0:
        return {
            'diagnosis': 'no telemetry available',
            'adjusted_setpoints': {'tank1': t1_sp, 'tank2': t2_sp},
            'anomaly_flags': {'tank1': False, 'tank2': False},
        }
    n = len(telemetry_window)

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
    lev1 = series('tank1', 'level')
    lev2 = series('tank2', 'level')

    def mean(xs):
        if not xs:
            return 0.0
        return sum(xs) / float(len(xs))

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        mid = m // 2
        if m % 2:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0

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
        if kk <= 0 or not xs:
            return []
        if len(xs) <= kk:
            return xs
        return xs[-kk:]

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
    if k < 1:
        k = 1
    w = min(10, n)
    if w < 1:
        w = 1
    wlong = min(16, n)
    if wlong < 1:
        wlong = 1

    def baseline(xs):
        if not xs:
            return 0.0
        m = len(xs)
        whole_q10 = quantile(xs, 0.10)
        early_n = max(2, min(8, m // 4))
        early = xs[:early_n]
        early_q20 = quantile(early, 0.20)
        return min(whole_q10, early_q20)

    ref1 = baseline(eff1)
    ref2 = baseline(eff2)

    te1 = median(tail(eff1, k))
    te2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))
    ml1 = mean(tail(lev1, k))
    ml2 = mean(tail(lev2, k))

    dev1 = te1 - ref1
    dev2 = te2 - ref2

    spike1 = (te1 > 3.0) and (ae1 > 0.22)
    abs1 = (te1 > 2.2) and (ae1 > 0.20)
    dev1_hi = (dev1 > 0.30) and (ae1 > 0.14)
    sig1 = frac_rel(eff1, err1, ref1, 0.32, 0.12, w)
    sust1 = sig1 >= 0.5
    level_evidence1 = (abs(ml1 - nom1) > 0.35) and (t1_sp < nom1 - 0.05)
    raw1 = bool(spike1 or abs1 or dev1_hi or sust1 or level_evidence1)

    spike2 = (te2 > 2.6) and (ae2 > 0.25)
    abs2 = (te2 > 1.8) and (ae2 > 0.18)
    dev2_hi = (dev2 > 0.25) and (ae2 > 0.12)
    sig2 = frac_rel(eff2, err2, ref2, 0.28, 0.10, w)
    sust2 = sig2 >= 0.5
    level_evidence2 = (abs(ml2 - nom2) > 0.35) and (t2_sp < nom2 - 0.05)
    raw2 = bool(spike2 or abs2 or dev2_hi or sust2 or level_evidence2)

    t2_long = frac_abs(eff2, err2, 1.8, 0.16, wlong) > 0.6
    t2_strong_dev = (dev2 > 0.45) and (ae2 > 0.14)
    t2_indep = bool(spike2 or t2_long or t2_strong_dev)

    h = k // 3
    if h < 3:
        h = 3
    if h > k:
        h = k

    rec1 = (k >= 4) and (mean(tail(eff1, h)) <= ref1 + 0.35) and (mean_abs(tail(err1, h)) <= 0.14)
    rec2 = (k >= 4) and (mean(tail(eff2, h)) <= ref2 + 0.30) and (mean_abs(tail(err2, h)) <= 0.12)

    tank1_anom = bool(raw1 and not rec1)

    upstream1 = bool(raw1 or (dev1 > 0.25 and ae1 > 0.10) or (te1 > 2.2))
    cascade = bool(upstream1 and raw2 and not t2_indep)
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.4
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (robust effort/error/level evidence)'
    elif (dev1 < 0.25) and (ae1 < 0.14) and (abs(ml1 - nom1) < 0.25) and (t1_sp < nom1):
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
        diag2 = 'tank2 anomaly suspected (robust independent evidence)'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif (dev2 < 0.20) and (ae2 < 0.12) and (abs(ml2 - nom2) < 0.25) and (t2_sp < nom2):
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
