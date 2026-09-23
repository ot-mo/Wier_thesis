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

    def frac_eff_high(effs, ref, margin, win):
        seg_e = tail(effs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        cnt = 0
        for e in seg_e:
            if e > ref + margin:
                cnt += 1
        return cnt / float(m)

    k = min(8, n)
    w = min(10, n)
    wlong = min(16, n)
    h = k // 3
    if h < 3:
        h = 3
    if h > k:
        h = k

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    ref1 = quantile(eff1, 0.20)
    ref2 = quantile(eff2, 0.20)

    dev1 = te1 - ref1
    dev2 = te2 - ref2

    f1_eff = frac_eff_high(eff1, ref1, 0.45, w)
    f2_eff = frac_eff_high(eff2, ref2, 0.35, w)

    # Tank 1: high-head source, larger effort scale.  Lower thresholds and
    # add effort-only persistence so a compensated leak is still caught.
    spike1 = (te1 > 3.00) and (ae1 > 0.20)
    abs1 = (te1 > 2.30) and (ae1 > 0.12)
    dev1_hi = (dev1 > 0.45) and (ae1 > 0.10)
    eff_only_sust1 = (te1 > ref1 + 0.45) and (f1_eff >= 0.55)
    sust1 = frac_rel(eff1, err1, ref1, 0.30, 0.10, w) >= 0.50
    raw1 = spike1 or abs1 or dev1_hi or eff_only_sust1 or sust1

    # Tank 2: gravity-fed, lower baseline and softer scale.
    spike2 = (te2 > 2.50) and (ae2 > 0.25)
    abs2 = (te2 > 1.85) and (ae2 > 0.12)
    dev2_hi = (dev2 > 0.40) and (ae2 > 0.10)
    eff_only_sust2 = (te2 > ref2 + 0.40) and (f2_eff >= 0.55)
    sust2 = frac_rel(eff2, err2, ref2, 0.25, 0.10, w) >= 0.50
    raw2 = spike2 or abs2 or dev2_hi or eff_only_sust2 or sust2

    # Independent evidence for tank2 when tank1 is also faulted.
    # A tank1 fault perturbs tank2 through cascade; only flag tank2 if its
    # deviation is larger than the expected coupled deviation plus margin,
    # or if it shows an unambiguous spike.
    t2_indep_dev = dev2 > (0.65 * max(dev1, 0.0) + 0.35)
    t2_indep_signal = t2_indep_dev or spike2

    recovery_eff1 = min(ref1 + 0.35, 2.40)
    recovery_err1 = 0.10
    rec1 = (k >= 4) and (mean(tail(eff1, h)) <= recovery_eff1) and (mean_abs(tail(err1, h)) <= recovery_err1)

    recovery_eff2 = min(ref2 + 0.30, 1.90)
    recovery_err2 = 0.10
    rec2 = (k >= 4) and (mean(tail(eff2, h)) <= recovery_eff2) and (mean_abs(tail(err2, h)) <= recovery_err2)

    tank1_anom = bool(raw1 and not rec1)

    upstream1 = bool(raw1 or (dev1 > 0.42) or (te1 > 2.25))
    cascade = bool(upstream1 and raw2 and (not t2_indep_signal))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort/error above healthy reference)'
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
    elif (dev2 < 0.28) and (ae2 < 0.12) and (t2_sp < nom2):
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
