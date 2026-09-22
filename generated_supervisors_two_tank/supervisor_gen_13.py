def supervise(telemetry_window, active_setpoints, nominal_targets):
    sp1 = float(active_setpoints["tank1"])
    sp2 = float(active_setpoints["tank2"])
    nom1 = float(nominal_targets["tank1"])
    nom2 = float(nominal_targets["tank2"])

    n = len(telemetry_window)
    if n == 0:
        return {
            "diagnosis": "no telemetry available",
            "adjusted_setpoints": {"tank1": sp1, "tank2": sp2},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    u1 = []
    u2 = []
    e1 = []
    e2 = []
    for step in telemetry_window:
        d1 = step["tank1"]
        d2 = step["tank2"]
        u1.append(abs(float(d1["pump_effort"])))
        u2.append(abs(float(d2["pump_effort"])))
        e1.append(abs(float(d1["error"])))
        e2.append(abs(float(d2["error"])))

    def mean(seq):
        total = 0.0
        for v in seq:
            total += v
        return total / len(seq)

    def smooth(seq, w):
        out = []
        for i in range(len(seq)):
            lo = i - w + 1
            if lo < 0:
                lo = 0
            out.append(mean(seq[lo:i + 1]))
        return out

    def low_q(seq, frac):
        ordered = sorted(seq)
        idx = int(frac * (len(ordered) - 1))
        if idx < 0:
            idx = 0
        return ordered[idx]

    ks = 4
    if ks > n:
        ks = n

    u1r = mean(u1[-ks:])
    u2r = mean(u2[-ks:])
    e1r = mean(e1[-ks:])
    e2r = mean(e2[-ks:])

    su1 = smooth(u1, 3)
    su2 = smooth(u2, 3)
    se1 = smooth(e1, 3)
    se2 = smooth(e2, 3)

    u1b = low_q(su1, 0.08)
    u2b = low_q(su2, 0.08)
    e1b = low_q(se1, 0.08)
    e2b = low_q(se2, 0.08)

    def ratio(recent, base):
        return (recent - base) / (base + 0.2)

    eu1 = ratio(u1r, u1b)
    eu2 = ratio(u2r, u2b)
    ee1 = ratio(e1r, e1b)
    ee2 = ratio(e2r, e2b)

    E_RATIO = 0.45
    E_ABS = 0.30
    ERR_RATIO = 0.90
    ERR_ABS = 0.25
    CASCADE_MARGIN = 0.15
    T1_SOFT_EFF = 0.12
    T1_SOFT_ERR = 0.30

    t1_effort = (eu1 > E_RATIO) and ((u1r - u1b) > E_ABS)
    t1_error = (ee1 > ERR_RATIO) and (e1r > ERR_ABS)
    tank1_anom = t1_effort or t1_error

    t1_perturbed = (eu1 > T1_SOFT_EFF) or (ee1 > T1_SOFT_ERR)

    need2 = E_RATIO
    if t1_perturbed:
        need2 += CASCADE_MARGIN
    t2_effort = (eu2 > need2) and ((u2r - u2b) > E_ABS)
    t2_error = (ee2 > ERR_RATIO) and (e2r > ERR_ABS) and (not t1_perturbed)
    tank2_anom = t2_effort or t2_error

    MAX_DEPRESS = 1.0
    LOWER_STEP = 0.35
    RESTORE_STEP = 1.5

    if tank1_anom:
        new1 = sp1 - LOWER_STEP
        if new1 < nom1 - MAX_DEPRESS:
            new1 = nom1 - MAX_DEPRESS
        msg1 = "tank1 anomaly suspected"
    elif sp1 < nom1:
        new1 = sp1 + RESTORE_STEP
        if new1 > nom1:
            new1 = nom1
        msg1 = "tank1 nominal, restoring setpoint"
    else:
        new1 = sp1
        msg1 = "tank1 nominal"

    if tank2_anom:
        new2 = sp2 - LOWER_STEP
        if new2 < nom2 - MAX_DEPRESS:
            new2 = nom2 - MAX_DEPRESS
        msg2 = "tank2 anomaly suspected"
    else:
        if sp2 < nom2:
            new2 = sp2 + RESTORE_STEP
            if new2 > nom2:
                new2 = nom2
            msg2 = "tank2 nominal, restoring setpoint"
        else:
            new2 = sp2
            msg2 = "tank2 nominal"
        if t1_perturbed and eu2 > E_RATIO:
            msg2 = "tank2 perturbation attributed to upstream tank1"

    return {
        "diagnosis": msg1 + "; " + msg2,
        "adjusted_setpoints": {"tank1": new1, "tank2": new2},
        "anomaly_flags": {"tank1": tank1_anom, "tank2": tank2_anom},
    }
