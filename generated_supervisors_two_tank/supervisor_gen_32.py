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
        cnt = 0
        for i in range(m):
            if seg_e[i] > ref + eff_margin and abs(seg_r[i]) > err_margin:
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
    w = min(10, n)
    wlong = min(16, n)
    early_len = min(20, n)
    if early_len < 3:
        early_len = n

    # Healthy references from the earliest telemetry, before most faults appear.
    base_eff1 = quantile(eff1[:early_len], 0.5)
    base_eff2 = quantile(eff2[:early_len], 0.5)
    base_abs_err1 = quantile([abs(x) for x in err1[:early_len]], 0.75)
    base_abs_err2 = quantile([abs(x) for x in err2[:early_len]], 0.75)

    # Lightweight linear coupling tank1 -> tank2 estimated on the healthy prefix.
    reg_n = early_len
    if reg_n > len(eff1):
        reg_n = len(eff1)
    if reg_n > len(eff2):
        reg_n = len(eff2)
    if reg_n >= 2:
        mx = sum(eff1[:reg_n]) / float(reg_n)
        my = sum(eff2[:reg_n]) / float(reg_n)
        cov = 0.0
        var = 0.0
        for i in range(reg_n):
            dx = eff1[i] - mx
            dy = eff2[i] - my
            cov += dx * dy
            var += dx * dx
        if var > 1e-9:
            a21 = cov / var
            b21 = my - a21 * mx
        else:
            a21 = 0.0
            b21 = my
    else:
        a21 = 0.0
        b21 = base_eff2
    if a21 < 0.0 or a21 > 5.0:
        a21 = 0.0
        b21 = base_eff2

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    dev1 = te1 - base_eff1
    dev2 = te2 - base_eff2
    pred_t2 = a21 * te1 + b21
    t2_excess = te2 - pred_t2

    def frac_excess2(eff1s, eff2s, errs, a, b, exc_margin, err_margin, win):
        seg1 = tail(eff1s, win)
        seg2 = tail(eff2s, win)
        seg_r = tail(errs, win)
        m = len(seg1)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            pred = a * seg1[i] + b
            excess = seg2[i] - pred
            if excess > exc_margin and abs(seg_r[i]) > err_margin:
                cnt += 1
        return cnt / float(m)

    # Tank 1 detector: upstream, larger effort scale.
    spike1 = (te1 > 3.0) and (ae1 > 0.22)
    abs1 = (te1 > 2.3) and (ae1 > 0.18)
    dev1_hi = (dev1 > 0.5) and (ae1 > 0.16)
    sus1 = frac_rel(eff1, err1, base_eff1, 0.4, 0.14, w) >= 0.5
    raw1 = spike1 or abs1 or dev1_hi or sus1

    # Tank 2 detector: lower baseline, plus independent excess over what is
    # expected from the tank1->tank2 coupling on the healthy prefix.
    spike2 = (te2 > 2.6) and (ae2 > 0.28)
    abs2 = (te2 > 1.8) and (ae2 > 0.20)
    dev2_hi = (dev2 > 0.45) and (ae2 > 0.15)
    sus2 = frac_rel(eff2, err2, base_eff2, 0.35, 0.14, w) >= 0.5
    excess2 = (t2_excess > 0.45) and (ae2 > 0.15)
    frac_ex2 = frac_excess2(eff1, eff2, err2, a21, b21, 0.4, 0.15, w) >= 0.5
    raw2 = spike2 or abs2 or dev2_hi or sus2 or excess2 or frac_ex2

    # Recovery: recent window must be back near the healthy prefix statistics.
    h = max(3, k // 2)
    if h > k:
        h = k
    rec1 = (k >= 4) and (mean(tail(eff1, h)) <= base_eff1 + 0.45) and (mean_abs(tail(err1, h)) <= max(base_abs_err1 + 0.08, 0.16))
    rec2 = (k >= 4) and (mean(tail(eff2, h)) <= base_eff2 + 0.40) and (mean_abs(tail(err2, h)) <= max(base_abs_err2 + 0.08, 0.15))

    tank1_anom = bool(raw1 and not rec1)

    # Cascade suppression: a tank1 fault can excite tank2 even when tank2 is
    # healthy.  Only allow tank2 to be flagged during an upstream event if its
    # evidence is clearly independent (spike, long organised absolute pattern,
    # or a sustained excess over the learned tank1->tank2 coupling).
    upstream1 = bool(raw1 or (dev1 > 0.35) or (te1 > 2.0))
    t2_long = frac_abs(eff2, err2, 1.8, 0.20, wlong) > 0.55
    t2_independent = bool(spike2 or t2_long or excess2 or frac_ex2)
    cascade = bool(upstream1 and raw2 and (not t2_independent))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort/error above healthy prefix)'
    elif (dev1 < 0.30) and (ae1 < max(base_abs_err1 + 0.08, 0.16)) and (t1_sp < nom1):
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
        diag2 = 'tank2 anomaly suspected (independent evidence)'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif (dev2 < 0.30) and (ae2 < max(base_abs_err2 + 0.08, 0.15)) and (t2_sp < nom2):
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