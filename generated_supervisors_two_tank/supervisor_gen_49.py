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

    if not isinstance(telemetry_window, (list, tuple)):
        telemetry_window = []
    n = len(telemetry_window)
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

    def tail(xs, k):
        if k <= 0:
            return []
        if len(xs) <= k:
            return xs
        return xs[len(xs) - k:]

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

    def frac_eff_only(effs, threshold, win):
        seg = tail(effs, win)
        if not seg:
            return 0.0
        cnt = 0
        for e in seg:
            if e > threshold:
                cnt += 1
        return cnt / float(len(seg))

    def theil_sen(xs, ys):
        if len(xs) < 2:
            return None, None
        slopes = []
        for i in range(len(xs)):
            xi = xs[i]
            for j in range(i + 1, len(xs)):
                dx = xs[j] - xi
                if abs(dx) > 1e-12:
                    slopes.append((ys[j] - ys[i]) / dx)
        if not slopes:
            return None, None
        s_sorted = sorted(slopes)
        m = len(s_sorted)
        if m % 2 == 1:
            slope = s_sorted[m // 2]
        else:
            slope = (s_sorted[m // 2 - 1] + s_sorted[m // 2]) / 2.0
        intercept_vals = [ys[i] - slope * xs[i] for i in range(len(xs))]
        intercept = median(intercept_vals)
        return slope, intercept

    def reg_window(xs, ys):
        if len(xs) <= 80:
            return xs, ys
        return xs[:20] + xs[-60:], ys[:20] + ys[-60:]

    early_n = max(3, min(8, n // 3))
    if early_n > n:
        early_n = n

    q_early1 = quantile(eff1[:early_n], 0.10)
    q_full1 = quantile(eff1, 0.10)
    q_early2 = quantile(eff2[:early_n], 0.10)
    q_full2 = quantile(eff2, 0.10)

    cap1 = 1.80
    cap2 = 1.50
    ref1 = min(q_early1, q_full1, cap1)
    ref2 = min(q_early2, q_full2, cap2)

    k = min(8, n)
    te1 = median(tail(eff1, k))
    te2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))
    dev1 = te1 - ref1
    dev2 = te2 - ref2

    w_det = min(9, n)
    sust_thresh1 = max(ref1 + 0.45, 2.05)
    sust1 = frac_eff_only(eff1, sust_thresh1, w_det) >= 0.6
    sust_thresh2 = max(ref2 + 0.35, 1.80)
    sust2 = frac_eff_only(eff2, sust_thresh2, w_det) >= 0.6

    raw1 = bool(
        te1 > 2.50 or
        (dev1 > 0.45 and ae1 > 0.06) or
        dev1 > 0.70 or
        sust1
    )
    raw2 = bool(
        te2 > 2.00 or
        (dev2 > 0.35 and ae2 > 0.06) or
        dev2 > 0.60 or
        sust2
    )

    reg_x, reg_y = reg_window(eff1, eff2)
    slope, intercept = theil_sen(reg_x, reg_y)
    if slope is None:
        base2 = median(eff2)
        res2 = [eff2[i] - base2 for i in range(n)]
    else:
        res2 = [eff2[i] - (intercept + slope * eff1[i]) for i in range(n)]
    res_tail = tail(res2, k)
    res_med = median(res_tail)

    independent2 = bool(
        res_med > 0.35 or
        (res_med > 0.22 and ae2 > 0.10) or
        (te2 > 2.80 and ae2 > 0.15)
    )

    depressed_eps = 0.05
    depressed1 = t1_sp < nom1 - depressed_eps
    depressed2 = t2_sp < nom2 - depressed_eps

    tank1_anom = True if depressed1 else raw1

    upstream1 = bool(
        tank1_anom or
        raw1 or
        te1 > 2.10 or
        dev1 > 0.35
    )

    if upstream1:
        tank2_anom_base = bool(raw2 and independent2)
    else:
        tank2_anom_base = bool(raw2 or independent2)

    tank2_anom = True if depressed2 else tank2_anom_base

    lower_step = 0.3
    restore_step = 0.8
    max_depress = 1.0

    if tank1_anom:
        if depressed1:
            if raw1:
                new1 = t1_sp
                diag1 = 'tank1 anomaly active, setpoint held depressed'
            else:
                new1 = nom1
                diag1 = 'tank1 latched, restoring setpoint to verify clearance'
        else:
            new1 = max(nom1 - max_depress, t1_sp - lower_step)
            diag1 = 'tank1 anomaly suspected, depressing setpoint'
    else:
        if depressed1:
            new1 = min(nom1, t1_sp + restore_step)
            diag1 = 'tank1 setpoint restoring'
        else:
            new1 = t1_sp
            diag1 = 'tank1 nominal'

    if tank2_anom:
        if depressed2:
            if raw2:
                new2 = t2_sp
                diag2 = 'tank2 anomaly active, setpoint held depressed'
            else:
                new2 = nom2
                diag2 = 'tank2 latched, restoring setpoint to verify clearance'
        else:
            new2 = max(nom2 - max_depress, t2_sp - lower_step)
            diag2 = 'tank2 anomaly suspected, depressing setpoint'
    else:
        if depressed2:
            new2 = min(nom2, t2_sp + restore_step)
            diag2 = 'tank2 setpoint restoring'
        else:
            new2 = t2_sp
            diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }