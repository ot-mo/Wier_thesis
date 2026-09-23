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

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m % 2 == 1:
            return float(s[m // 2])
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    def tail(xs, kk):
        if kk <= 0:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_above(effs, errs, eff_bar, err_bar, win):
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

    eff1 = series('tank1', 'pump_effort')
    err1 = series('tank1', 'error')
    eff2 = series('tank2', 'pump_effort')
    err2 = series('tank2', 'error')

    base_len = int(n * 0.25)
    if base_len < 5:
        base_len = 5
    if base_len > n:
        base_len = n

    base_eff1 = median(eff1[:base_len])
    base_eff2 = median(eff2[:base_len])
    base_err1 = median([abs(x) for x in err1[:base_len]])
    base_err2 = median([abs(x) for x in err2[:base_len]])

    k = min(8, n)
    w = min(12, n)
    wlong = min(20, n)
    h = min(max(3, k // 2), n)

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    # Tank 1 detection
    b1_eff = base_eff1
    b1_err = base_err1
    spike1 = (te1 > max(b1_eff + 1.2, 3.2)) and (ae1 > 0.22)
    abs1 = (te1 > max(b1_eff + 0.8, 2.3)) and (ae1 > 0.18)
    dev1_hi = (te1 > b1_eff + 0.45) and (ae1 > 0.14)
    sust1_frac = frac_above(eff1, err1, b1_eff + 0.40, 0.12, w)
    sust1 = sust1_frac >= 0.60
    raw1 = spike1 or abs1 or dev1_hi or sust1

    rec1 = (mean(tail(eff1, h)) <= b1_eff + 0.35) and (mean_abs(tail(err1, h)) <= max(b1_err + 0.05, 0.10))

    # Tank 2 detection
    b2_eff = base_eff2
    b2_err = base_err2
    spike2 = (te2 > max(b2_eff + 1.0, 2.8)) and (ae2 > 0.26)
    abs2 = (te2 > max(b2_eff + 0.7, 2.0)) and (ae2 > 0.18)
    dev2_hi = (te2 > b2_eff + 0.35) and (ae2 > 0.12)
    sust2_frac = frac_above(eff2, err2, b2_eff + 0.30, 0.10, w)
    sust2 = sust2_frac >= 0.50
    raw2 = spike2 or abs2 or dev2_hi or sust2

    t2_long_frac = frac_above(eff2, err2, max(b2_eff + 0.9, 2.0), 0.18, wlong)
    t2_long = t2_long_frac > 0.50
    strong2 = spike2 or t2_long or (sust2 and (te2 > b2_eff + 0.7))

    rec2 = (mean(tail(eff2, h)) <= b2_eff + 0.30) and (mean_abs(tail(err2, h)) <= max(b2_err + 0.05, 0.10))

    tank1_anom = bool(raw1 and not rec1)

    upstream_evidence = tank1_anom or (te1 > b1_eff + 0.50 and ae1 > 0.12)

    tank2_candidate = bool(raw2 and not rec2)
    cascade = False
    if tank2_candidate:
        if upstream_evidence and not strong2:
            cascade = True
            tank2_anom = False
        else:
            tank2_anom = True
    else:
        tank2_anom = False

    LOWER_STEP = 0.3
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort/error above healthy baseline)'
    elif (te1 <= b1_eff + 0.20) and (ae1 <= max(b1_err + 0.05, 0.08)) and (t1_sp < nom1):
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
    elif (te2 <= b2_eff + 0.20) and (ae2 <= max(b2_err + 0.05, 0.08)) and (t2_sp < nom2):
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