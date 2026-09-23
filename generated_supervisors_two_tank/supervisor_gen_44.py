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

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

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

    def frac_cond(effs, errs, cond_eff, cond_err, win):
        seg_e = tail(effs, win)
        seg_r = tail(errs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if seg_e[i] > cond_eff and abs(seg_r[i]) > cond_err:
                cnt += 1
        return cnt / float(m)

    def frac_eff_only(effs, threshold, win):
        seg = tail(effs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for e in seg:
            if e > threshold:
                cnt += 1
        return cnt / float(m)

    def stdev(xs):
        m = len(xs)
        if m < 2:
            return 0.0
        avg = sum(xs) / float(m)
        var = sum((x - avg) ** 2 for x in xs) / float(m - 1)
        return var ** 0.5

    k = min(6, n)
    w = min(12, n)
    wlong = min(16, n)
    h = max(3, min(4, k))

    early_n = max(5, min(12, n // 2))
    if early_n > n:
        early_n = n

    early1 = eff1[:early_n]
    early2 = eff2[:early_n]
    q_early1 = quantile(early1, 0.10)
    q_early2 = quantile(early2, 0.10)
    q_full1 = quantile(eff1, 0.10)
    q_full2 = quantile(eff2, 0.10)
    ref1 = min(q_early1, q_full1)
    ref2 = min(q_early2, q_full2)

    # Regression of tank2 effort on tank1 effort over early (pre-fault) window
    if n >= 4 and len(early1) >= 3:
        m1 = mean(early1)
        m2 = mean(early2)
        var1 = sum((x - m1) ** 2 for x in early1)
        if var1 > 1e-9:
            cov = sum((early1[i] - m1) * (early2[i] - m2) for i in range(len(early1)))
            slope = cov / var1
            intercept = m2 - slope * m1
            resid2 = [eff2[i] - (slope * eff1[i] + intercept) for i in range(n)]
            base_res2 = median(resid2[:early_n])
            std_res2 = stdev(resid2[:early_n])
        else:
            resid2 = [eff2[i] - ref2 for i in range(n)]
            base_res2 = median(resid2[:early_n])
            std_res2 = stdev(resid2[:early_n])
    else:
        resid2 = [eff2[i] - ref2 for i in range(n)]
        base_res2 = median(resid2[:early_n])
        std_res2 = stdev(resid2[:early_n])

    te1 = median(tail(eff1, k))
    te2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))
    dev1 = te1 - ref1
    dev2 = te2 - ref2

    rmed2 = median(tail(resid2, k))
    rdev2 = rmed2 - base_res2

    # Tank 1 raw detection
    abs_eff1 = te1 > 2.20
    spike1 = te1 > 2.80
    dev1_anom = (dev1 > 0.35 and ae1 > 0.05) or (dev1 > 0.55)
    sustained1 = frac_eff_only(eff1, max(ref1 + 0.25, 1.90), wlong) >= 0.55
    raw1 = bool(abs_eff1 or spike1 or dev1_anom or sustained1)

    # Tank 2 raw detection
    abs_eff2 = te2 > 1.75
    spike2 = te2 > 2.30
    dev2_anom = (dev2 > 0.30 and ae2 > 0.05) or (dev2 > 0.50)
    sustained2 = frac_eff_only(eff2, max(ref2 + 0.20, 1.55), wlong) >= 0.55
    raw2 = bool(abs_eff2 or spike2 or dev2_anom or sustained2)

    # Independent tank2 residual signature
    indep2 = bool(
        (rdev2 > 0.35 and ae2 > 0.05) or
        (std_res2 > 0.1 and rdev2 > 3.0 * std_res2) or
        (frac_eff_only(resid2, base_res2 + 0.20, wlong) >= 0.6)
    )

    # Strong tank2 override: independent signature or large raw shift
    t2_strong = bool(
        indep2 or
        (te2 > 2.30 and ae2 > 0.10) or
        (dev2 > 0.55) or
        (frac_cond(eff2, err2, 1.70, 0.12, wlong) > 0.55) or
        (frac_eff_only(eff2, 1.80, wlong) > 0.80)
    )

    # Recovery checks
    rec1 = (median(tail(eff1, h)) <= ref1 + 0.20) and (mean_abs(tail(err1, h)) <= 0.10)
    rmed_rec2 = median(tail(resid2, h))
    rec2 = (
        (median(tail(eff2, h)) <= ref2 + 0.20) and
        (mean_abs(tail(err2, h)) <= 0.10) and
        (rmed_rec2 <= base_res2 + 0.15)
    )

    tank1_anom = bool(raw1 and not rec1)

    upstream1 = bool(raw1 or dev1 > 0.30 or te1 > 2.00)
    cascade = bool(upstream1 and raw2 and (not t2_strong))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.4
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected'
    elif (dev1 < 0.20) and (ae1 < 0.10) and (t1_sp < nom1):
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
        diag2 = 'tank2 anomaly suspected'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault'
    elif (dev2 < 0.20) and (ae2 < 0.10) and (t2_sp < nom2):
        if rmed_rec2 <= base_res2 + 0.15:
            new2 = min(nom2, t2_sp + RESTORE_STEP)
            diag2 = 'tank2 stable, restoring toward nominal'
        else:
            new2 = t2_sp
            diag2 = 'tank2 nominal'
    else:
        new2 = t2_sp
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }