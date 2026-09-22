def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_sp = float(active_setpoints["tank1"])
    tank2_sp = float(active_setpoints["tank2"])
    nom1 = float(nominal_targets["tank1"])
    nom2 = float(nominal_targets["tank2"])

    n = len(telemetry_window)
    if n == 0:
        return {
            "diagnosis": "no telemetry available",
            "adjusted_setpoints": {"tank1": tank1_sp, "tank2": tank2_sp},
            "anomaly_flags": {"tank1": False, "tank2": False},
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

    eff1 = col("tank1", "pump_effort")
    eff2 = col("tank2", "pump_effort")
    err1 = col("tank1", "error")
    err2 = col("tank2", "error")

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
        pos = q * (m - 1)
        lo = int(pos)
        hi = lo + 1
        if hi >= m:
            return s[-1]
        fr = pos - lo
        return s[lo] * (1.0 - fr) + s[hi] * fr

    def frac_above(xs, thr, w):
        start = len(xs) - w
        if start < 0:
            start = 0
        tot = 0
        cnt = 0
        for i in range(start, len(xs)):
            tot += 1
            if xs[i] > thr:
                cnt += 1
        if tot == 0:
            return 0.0
        return cnt / float(tot)

    # fast horizon for "current" effort, sustained horizon for voting,
    # shorter horizon for recovery so the flag clears promptly after a fault.
    h = min(6, n)
    if h < 1:
        h = 1
    m = min(15, n)
    if m < 1:
        m = 1
    mr = min(10, n)
    if mr < 1:
        mr = 1

    # ---- tank 1: high-head source, larger absolute effort scale ----------
    ref1 = quantile(eff1, 0.15)
    spread1 = quantile(eff1, 0.55) - quantile(eff1, 0.10)
    if spread1 < 0.05:
        spread1 = 0.05
    margin1 = 1.0 * spread1
    if margin1 < 0.30:
        margin1 = 0.30
    if margin1 > 0.85:
        margin1 = 0.85
    bar1 = ref1 + margin1
    abs1 = ref1 + 1.2 * margin1
    if abs1 < 2.9:
        abs1 = 2.9
    errb1 = 0.18

    # ---- tank 2: gravity fed, lower baseline, weaker absolute signal ----
    ref2 = quantile(eff2, 0.15)
    spread2 = quantile(eff2, 0.55) - quantile(eff2, 0.10)
    if spread2 < 0.05:
        spread2 = 0.05
    margin2 = 1.0 * spread2
    if margin2 < 0.25:
        margin2 = 0.25
    if margin2 > 0.70:
        margin2 = 0.70
    bar2 = ref2 + margin2
    abs2 = ref2 + 1.2 * margin2
    if abs2 < 2.4:
        abs2 = 2.4
    errb2 = 0.14

    def detect(effs, errs, ref, bar, abs_level, ebar):
        r = mean(effs[-h:])
        ae = mean_abs(errs[-h:])
        f = frac_above(effs, bar, m)
        fabs = frac_above(effs, abs_level, m)
        # sustained rise above this tank's own calm operating point
        by_rel = (r > bar) and (f >= 0.45)
        # sustained push against this tank's absolute ceiling
        by_abs = (fabs >= 0.45) and (r > ref + 0.6 * (abs_level - ref))
        # clear, sustained control error together with an effort rise
        by_err = (ae > 1.25 * ebar) and (r > ref + 0.6 * (bar - ref)) and (f >= 0.50)
        return bool(by_rel or by_abs or by_err)

    def calm(effs, errs, ref, bar, ebar):
        if n < h + 1:
            return False
        r = mean(effs[-h:])
        ae = mean_abs(errs[-h:])
        lo = ref + 0.5 * (bar - ref)
        f = frac_above(effs, lo, mr)
        if f > 0.25:
            return False
        if r > lo:
            return False
        if ae > ebar:
            return False
        return True

    def settled(effs, ref, bar):
        # effort-only test used to decide that a tank may be restored:
        # it does not require a tiny error, so our own restore step cannot
        # keep the tank locked in the depressed state.
        if n < h + 1:
            return False
        r = mean(effs[-h:])
        lo = ref + 0.6 * (bar - ref)
        f = frac_above(effs, lo, mr)
        return bool((f <= 0.30) and (r <= lo))

    raw1 = detect(eff1, err1, ref1, bar1, abs1, errb1)
    raw2 = detect(eff2, err2, ref2, bar2, abs2, errb2)
    calm1 = calm(eff1, err1, ref1, bar1, errb1)
    calm2 = calm(eff2, err2, ref2, bar2, errb2)
    set1 = settled(eff1, ref1, bar1)
    set2 = settled(eff2, ref2, bar2)

    tank1_anom = bool(raw1 and not calm1)

    # Cascade: tank1 hydraulically feeds tank2, so a tank1 fault changes
    # tank2's inflow and its PID then holds an elevated effort even when
    # tank2 is healthy. Suppress tank2's own verdict only for an echo that
    # is neither strong nor organised over the whole horizon; an independent
    # tank2 fault is strong (above its absolute ceiling) or sustained over
    # the entire window and therefore survives the suppression.
    w2 = min(20, n)
    f2_long = frac_above(eff2, bar2, w2)
    strong2 = bool(mean(eff2[-h:]) > abs2)
    organised2 = bool(f2_long >= 0.80)
    cascade = bool(tank1_anom and raw2 and (not strong2) and (not organised2))
    tank2_anom = bool(raw2 and not calm2 and not cascade)

    LOWER_STEP = 0.25
    RESTORE_STEP = 0.40
    MAX_DEPRESS = 0.90

    if tank1_anom:
        new1 = max(nom1 - MAX_DEPRESS, tank1_sp - LOWER_STEP)
        diag1 = "tank1 anomaly suspected (sustained effort above its calm baseline)"
    elif tank1_sp < nom1 and set1:
        new1 = min(nom1, tank1_sp + RESTORE_STEP)
        diag1 = "tank1 calm, restoring toward nominal"
    else:
        new1 = tank1_sp
        diag1 = "tank1 nominal"

    if tank2_anom:
        new2 = max(nom2 - MAX_DEPRESS, tank2_sp - LOWER_STEP)
        diag2 = "tank2 anomaly suspected (independent, sustained evidence)"
    elif cascade:
        new2 = tank2_sp
        diag2 = "tank2 perturbation attributed to upstream tank1 fault"
    elif tank2_sp < nom2 and set2:
        new2 = min(nom2, tank2_sp + RESTORE_STEP)
        diag2 = "tank2 calm, restoring toward nominal"
    else:
        new2 = tank2_sp
        diag2 = "tank2 nominal"

    return {
        "diagnosis": diag1 + "; " + diag2,
        "adjusted_setpoints": {"tank1": new1, "tank2": new2},
        "anomaly_flags": {"tank1": tank1_anom, "tank2": tank2_anom},
    }