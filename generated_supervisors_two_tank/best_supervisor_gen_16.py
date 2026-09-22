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

    k = min(10, n)
    if k < 1:
        k = 1

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
        frac = pos - lo
        return s[lo] * (1.0 - frac) + s[hi] * frac

    def frac_hi(effs, errs, bar, errbar, w):
        m = len(effs)
        start = m - w
        if start < 0:
            start = 0
        cnt = 0
        tot = 0
        for i in range(start, m):
            tot += 1
            if effs[i] > bar and abs(errs[i]) > errbar:
                cnt += 1
        if tot == 0:
            return 0.0
        return cnt / float(tot)

    te1 = mean(eff1[-k:])
    te2 = mean(eff2[-k:])
    ae1 = mean_abs(err1[-k:])
    ae2 = mean_abs(err2[-k:])
    be1 = mean(err1[-k:])
    be2 = mean(err2[-k:])

    # calm reference effort for each tank (robust to a fault filling part of the
    # window); this is the operating point a healthy tank should return to.
    q1 = quantile(eff1, 0.2)
    q2 = quantile(eff2, 0.2)

    split = n - k
    if split >= 3:
        base1 = mean(eff1[:split])
        base2 = mean(eff2[:split])
    else:
        base1 = te1
        base2 = te2

    rise1 = te1 - base1
    rise2 = te2 - base2

    # ---- recovery test: genuine return to the healthy operating point ----
    # A live fault that merely plateaus after its onset must NOT be treated as
    # recovered, so require the recent effort to be near the calm reference and
    # below a tank-specific ceiling, with a small recent error.
    def recovering(errs, effs, ref, cap, errcap):
        if k < 5:
            return False
        h = k // 3
        if h < 2:
            h = 2
        recent_eff = mean(effs[-h:])
        recent_err = mean_abs(errs[-h:])
        if recent_eff > cap:
            return False
        if recent_err > errcap:
            return False
        return recent_eff <= ref + 0.40

    # tank1 runs a higher head (larger effort scale); tank2 is gravity fed.
    rec1 = recovering(err1, eff1, q1, 3.0, 0.16)
    rec2 = recovering(err2, eff2, q2, 2.3, 0.13)

    # ---- Tank 1: high-head source, larger absolute effort scale ----
    t1_strong = te1 > 3.4
    t1_abs = (te1 > 2.5) and (ae1 > 0.25)
    t1_base = base1 if base1 > 0.5 else 0.5
    t1_rel = (rise1 > 0.45) and (rise1 > 0.25 * t1_base) and ((ae1 > 0.16) or (abs(be1) > 0.18))
    raw1 = t1_strong or t1_abs or t1_rel

    # ---- Tank 2: gravity-fed, lower baseline, weaker absolute signal ----
    t2_strong = (te2 > 2.9) and (ae2 > 0.32)
    t2_abs = (te2 > 2.0) and (ae2 > 0.20)
    t2_base = base2 if base2 > 0.5 else 0.5
    t2_rel = (rise2 > 0.35) and (rise2 > 0.22 * t2_base) and ((ae2 > 0.14) or (abs(be2) > 0.16))
    raw2 = t2_strong or t2_abs or t2_rel

    # ---- sustained, organised tank2 signature (over a longer horizon) ----
    w2 = min(16, n)
    f2_sustained = frac_hi(eff2, err2, 2.0, 0.20, w2)
    t2_sustained = f2_sustained > 0.7

    tank1_anom = bool(raw1 and not rec1)

    # Cascade: tank1 hydraulically feeds tank2, so a tank1 fault perturbs
    # tank2 even when tank2 is healthy (its PID compensates the changed inflow
    # and holds an elevated effort). Suppress tank2's own evidence only when it
    # can plausibly be an upstream echo - i.e. it is not unambiguously strong
    # AND it is not a sustained, organised signature. A genuine tank2 fault
    # persists over the whole horizon and therefore survives the suppression.
    cascade = bool(tank1_anom and raw2 and (not t2_strong) and (not t2_sustained))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        new1 = max(nom1 - MAX_DEPRESS, tank1_sp - LOWER_STEP)
        diag1 = "tank1 anomaly suspected (effort/error above own baseline)"
    elif (rise1 < 0.25) and (ae1 < 0.18) and tank1_sp < nom1:
        new1 = min(nom1, tank1_sp + RESTORE_STEP)
        diag1 = "tank1 stable, restoring toward nominal"
    else:
        new1 = tank1_sp
        diag1 = "tank1 nominal"

    if tank2_anom:
        new2 = max(nom2 - MAX_DEPRESS, tank2_sp - LOWER_STEP)
        diag2 = "tank2 anomaly suspected (independent, sustained evidence)"
    elif cascade:
        new2 = tank2_sp
        diag2 = "tank2 perturbation attributed to upstream tank1 fault"
    elif (rise2 < 0.25) and (ae2 < 0.18) and tank2_sp < nom2:
        new2 = min(nom2, tank2_sp + RESTORE_STEP)
        diag2 = "tank2 stable, restoring toward nominal"
    else:
        new2 = tank2_sp
        diag2 = "tank2 nominal"

    return {
        "diagnosis": diag1 + "; " + diag2,
        "adjusted_setpoints": {"tank1": new1, "tank2": new2},
        "anomaly_flags": {"tank1": tank1_anom, "tank2": tank2_anom},
    }
