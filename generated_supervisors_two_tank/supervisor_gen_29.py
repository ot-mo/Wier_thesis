def supervise(telemetry_window, active_setpoints, nominal_targets):
    def _num(src, key, default):
        try:
            if not isinstance(src, dict):
                return float(default)
            v = src.get(key, default)
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

    def baseline(xs):
        if not xs:
            return 0.0
        m = min(8, len(xs))
        early = xs[:m]
        return quantile(early, 0.20)

    def frac_high(xs, ref, margin, win):
        seg = tail(xs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for x in seg:
            if x > ref + margin:
                cnt += 1
        return cnt / float(m)

    def frac_rel(xs, ys, ref, eff_margin, err_margin, win):
        seg_x = tail(xs, win)
        seg_y = tail(ys, win)
        m = len(seg_x)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if seg_x[i] > ref + eff_margin and abs(seg_y[i]) > err_margin:
                cnt += 1
        return cnt / float(m)

    def frac_abs(xs, ys, eff_bar, err_bar, win):
        seg_x = tail(xs, win)
        seg_y = tail(ys, win)
        m = len(seg_x)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if seg_x[i] > eff_bar and abs(seg_y[i]) > err_bar:
                cnt += 1
        return cnt / float(m)

    k = min(10, n)
    w = min(12, n)
    wlong = min(20, n)

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    ref1 = baseline(eff1)
    ref2 = baseline(eff2)

    dev1 = te1 - ref1
    dev2 = te2 - ref2

    fhigh1 = frac_high(eff1, ref1, 0.35, w)
    fhigh2 = frac_high(eff2, ref2, 0.30, w)
    frel1 = frac_rel(eff1, err1, ref1, 0.35, 0.08, w)
    frel2 = frac_rel(eff2, err2, ref2, 0.30, 0.07, w)

    spike1 = te1 > 3.2 and ae1 > 0.15
    high_duration1 = fhigh1 >= 0.70 and dev1 > 0.35
    sustained1 = fhigh1 >= 0.55 and dev1 > 0.50
    rel1 = frel1 >= 0.60 and dev1 > 0.25
    abs1 = te1 > 2.8 and ae1 > 0.10
    raw1 = spike1 or high_duration1 or sustained1 or rel1 or abs1

    spike2 = te2 > 2.7 and ae2 > 0.10
    high_duration2 = fhigh2 >= 0.65 and dev2 > 0.30
    sustained2 = fhigh2 >= 0.50 and dev2 > 0.45
    rel2 = frel2 >= 0.60 and dev2 > 0.20
    abs2 = te2 > 2.4 and ae2 > 0.08
    raw2 = spike2 or high_duration2 or sustained2 or rel2 or abs2

    h = min(6, k)
    if h < 3:
        h = min(3, n)
    rec_eff1 = mean(tail(eff1, h))
    rec_err1 = mean_abs(tail(err1, h))
    rec_high1 = frac_high(eff1, ref1, 0.30, h)
    rec1 = (h >= 3) and (rec_eff1 <= ref1 + 0.65) and (rec_err1 <= 0.18) and (rec_high1 <= 0.25)

    rec_eff2 = mean(tail(eff2, h))
    rec_err2 = mean_abs(tail(err2, h))
    rec_high2 = frac_high(eff2, ref2, 0.25, h)
    rec2 = (h >= 3) and (rec_eff2 <= ref2 + 0.55) and (rec_err2 <= 0.16) and (rec_high2 <= 0.25)

    tank1_anom = bool(raw1 and not rec1)

    upstream1 = bool(raw1 or (dev1 > 0.40) or (te1 > 2.2))
    t2_strong_for_cascade = (
        (te2 > 2.7 and ae2 > 0.12) or
        (fhigh2 >= 0.80 and dev2 > 0.60) or
        (frac_abs(eff2, err2, 2.2, 0.08, wlong) > 0.60)
    )
    cascade = bool(upstream1 and raw2 and (not t2_strong_for_cascade))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.4
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort above healthy baseline)'
    elif (rec_eff1 <= ref1 + 0.65) and (rec_err1 <= 0.18) and (frac_high(eff1, ref1, 0.30, w) <= 0.30) and (t1_sp < nom1):
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
    elif (rec_eff2 <= ref2 + 0.55) and (rec_err2 <= 0.16) and (frac_high(eff2, ref2, 0.25, w) <= 0.30) and (t2_sp < nom2):
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