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
    n = len(eff1)

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

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m % 2 == 1:
            return s[m // 2]
        return 0.5 * (s[m // 2 - 1] + s[m // 2])

    def tail(xs, kk):
        if kk <= 0:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_above(xs, thr, win):
        seg = tail(xs, win)
        if not seg:
            return 0.0
        return sum(1 for x in seg if x > thr) / float(len(seg))

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
    w = min(8, n)
    wlong = min(16, n)

    # Detection baseline: low quantile of whole window (same spirit as original)
    det_base1 = quantile(eff1, 0.15)
    det_base2 = quantile(eff2, 0.15)

    # Recovery baseline: min of whole-window low quantile and early-window low quantile
    early_n = min(10, n)
    early_base1 = quantile(eff1[:early_n], 0.15) if early_n > 0 else det_base1
    early_base2 = quantile(eff2[:early_n], 0.15) if early_n > 0 else det_base2
    rec_base1 = min(det_base1, early_base1)
    rec_base2 = min(det_base2, early_base2)

    recent_eff1 = mean(tail(eff1, k))
    recent_eff2 = mean(tail(eff2, k))
    med_eff1 = median(tail(eff1, k))
    med_eff2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    dev1 = recent_eff1 - det_base1
    dev2 = recent_eff2 - det_base2
    dev_med1 = med_eff1 - det_base1
    dev_med2 = med_eff2 - det_base2

    # Effort-only persistence (does not require error to stay large)
    frac_hi_eff1 = frac_above(eff1, det_base1 + 0.25, w)
    frac_hi_eff2 = frac_above(eff2, det_base2 + 0.20, w)
    frac_hi_eff1_long = frac_above(eff1, det_base1 + 0.20, wlong)
    frac_hi_eff2_long = frac_above(eff2, det_base2 + 0.25, wlong)

    shift_eff1 = (dev_med1 > 0.25) and (frac_hi_eff1 >= 0.75) and (recent_eff1 > det_base1 + 0.25)
    shift_eff2 = (dev_med2 > 0.20) and (frac_hi_eff2 >= 0.75) and (recent_eff2 > det_base2 + 0.20)

    # Classic error-gated signatures
    sig1 = frac_rel(eff1, err1, det_base1, 0.45, 0.14, w)
    sust1 = sig1 >= 0.5
    sig2 = frac_rel(eff2, err2, det_base2, 0.40, 0.12, w)
    sust2 = sig2 >= 0.5

    spike1 = (recent_eff1 > 3.4) and (ae1 > 0.22)
    abs1 = (recent_eff1 > 2.5) and (ae1 > 0.22)
    dev_hi1 = (dev1 > 0.55) and (ae1 > 0.16)

    spike2 = (recent_eff2 > 2.9) and (ae2 > 0.30)
    abs2 = (recent_eff2 > 2.0) and (ae2 > 0.20)
    dev_hi2 = (dev2 > 0.45) and (ae2 > 0.14)

    raw1 = spike1 or abs1 or dev_hi1 or sust1 or shift_eff1
    raw2 = spike2 or abs2 or dev_hi2 or sust2 or shift_eff2

    # Setpoint memory: if we previously lowered a setpoint, treat that tank as
    # already suspected and require a clean recovery to clear it.
    dep1 = t1_sp < nom1 - 0.05
    dep2 = t2_sp < nom2 - 0.05

    rec1 = (
        (recent_eff1 <= rec_base1 + 0.35) and
        (med_eff1 <= rec_base1 + 0.30) and
        (recent_eff1 <= 2.3) and
        (med_eff1 <= 2.3) and
        (ae1 <= 0.14) and
        (frac_above(eff1, rec_base1 + 0.35, w) <= 0.2)
    )
    rec2 = (
        (recent_eff2 <= rec_base2 + 0.30) and
        (med_eff2 <= rec_base2 + 0.25) and
        (recent_eff2 <= 1.8) and
        (med_eff2 <= 1.8) and
        (ae2 <= 0.14) and
        (frac_above(eff2, rec_base2 + 0.30, w) <= 0.2)
    )

    tank1_anom = bool((raw1 or dep1) and not rec1)

    # Strong independent tank2 evidence that can override cascade suppression.
    t2_strong_shift = (
        (dev_med2 > 0.40) and
        (frac_hi_eff2_long >= 0.7) and
        (recent_eff2 > det_base2 + 0.40)
    )
    t2_long_abs = frac_abs(eff2, err2, 2.0, 0.20, wlong) > 0.6
    strong2 = spike2 or t2_long_abs or t2_strong_shift

    upstream1 = raw1 or dep1
    tank2_anom_pre = bool((raw2 or dep2) and not rec2)
    cascade_suppress = bool(upstream1 and tank2_anom_pre and (not strong2) and (not dep2))
    tank2_anom = bool(tank2_anom_pre and not cascade_suppress)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort/error above healthy reference)'
    elif (dev1 < 0.30) and (ae1 < 0.16) and (t1_sp < nom1):
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
    elif cascade_suppress:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif (dev2 < 0.30) and (ae2 < 0.16) and (t2_sp < nom2):
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