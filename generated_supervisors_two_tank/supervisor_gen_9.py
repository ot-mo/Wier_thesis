def supervise(telemetry_window, active_setpoints, nominal_targets):
    setpoint1 = float(active_setpoints["tank1"])
    setpoint2 = float(active_setpoints["tank2"])
    nominal1 = float(nominal_targets["tank1"])
    nominal2 = float(nominal_targets["tank2"])

    if not telemetry_window:
        return {
            "diagnosis": "no telemetry: holding current setpoints",
            "adjusted_setpoints": {"tank1": setpoint1, "tank2": setpoint2},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    eff1 = []
    eff2 = []
    ae1 = []
    ae2 = []
    for step in telemetry_window:
        t1 = step["tank1"]
        t2 = step["tank2"]
        eff1.append(float(t1["pump_effort"]))
        eff2.append(float(t2["pump_effort"]))
        ae1.append(abs(float(t1["error"])))
        ae2.append(abs(float(t2["error"])))

    n = len(eff1)

    def quantile(values, q):
        ordered = sorted(values)
        m = len(ordered)
        if m == 0:
            return 0.0
        idx = int(q * float(m - 1))
        if idx < 0:
            idx = 0
        if idx > m - 1:
            idx = m - 1
        return ordered[idx]

    def mean(values):
        if not values:
            return 0.0
        return sum(values) / float(len(values))

    def frac(flags):
        if not flags:
            return 0.0
        return sum(1 for f in flags if f) / float(len(flags))

    k_recent = 15 if n > 15 else n
    k_wide = 40 if n > 40 else n

    def analyze(eff, aerr, eff_margin, err_margin):
        # Self-calibrating healthy band: the two tanks have different physical
        # parameters (tank 2 is partly fed by tank 1's gravity drain), so the
        # band is derived from each tank's own low-order statistics instead of
        # fixed absolute numbers that sit inside the normal operating range.
        base_eff = quantile(eff, 0.15)
        base_err = quantile(aerr, 0.15)

        mild_eff = base_eff + max(0.18, 0.10 * abs(base_eff)) + eff_margin
        high_eff = base_eff + max(0.55, 0.30 * abs(base_eff)) + eff_margin
        mild_err = base_err + max(0.12, 0.40 * abs(base_err)) + err_margin
        high_err = base_err + max(0.45, 1.20 * abs(base_err)) + err_margin

        eff_r = eff[n - k_recent:]
        err_r = aerr[n - k_recent:]
        eff_w = eff[n - k_wide:]
        err_w = aerr[n - k_wide:]

        joint_r = []
        for i in range(k_recent):
            joint_r.append(eff_r[i] > mild_eff and err_r[i] > mild_err)
        joint_w = []
        for i in range(k_wide):
            joint_w.append(eff_w[i] > mild_eff and err_w[i] > mild_err)

        f_joint_r = frac(joint_r)
        f_joint_w = frac(joint_w)
        f_err_r = frac([v > high_err for v in err_r])
        f_eff_r = frac([v > high_eff for v in eff_r])
        mean_eff_r = mean(eff_r)
        mean_err_r = mean(err_r)
        mean_eff_w = mean(eff_w)
        mean_err_w = mean(err_w)

        reasons = []
        if f_joint_r >= 0.5:
            reasons.append("recent effort and error jointly elevated")
        if f_joint_w >= 0.5 and mean_err_w > mild_err:
            reasons.append("effort and error elevated across window")
        if f_err_r >= 0.6:
            reasons.append("large persistent tracking error")
        if f_eff_r >= 0.7 and mean_err_r > mild_err:
            reasons.append("high pump effort with residual error")
        if mean_eff_w > high_eff and mean_err_w > mild_err:
            reasons.append("mean effort and residual error above healthy band")

        anomaly = len(reasons) > 0
        calm = (mean_eff_r < mild_eff and mean_err_r < mild_err and f_joint_r < 0.2)
        return anomaly, calm, reasons

    t1_anomaly, t1_calm, t1_reasons = analyze(eff1, ae1, 0.0, 0.0)

    # Cascade coordination: tank 1 drains into tank 2, so a tank-1 fault
    # perturbs tank 2 even when tank 2 itself is healthy. While tank 1 is
    # flagged, tank 2 must show slightly more of its own evidence before it is
    # flagged too - this suppresses upstream-driven false positives while a
    # genuinely simultaneous (both-tanks) fault still trips on strong evidence.
    if t1_anomaly:
        t2_anomaly, t2_calm, t2_reasons = analyze(eff2, ae2, 0.20, 0.06)
    else:
        t2_anomaly, t2_calm, t2_reasons = analyze(eff2, ae2, 0.0, 0.0)

    LOWER_STEP = 0.10
    RESTORE_STEP = 0.40
    FLOOR_FRAC = 0.7

    def next_setpoint(sp, nom, anomaly):
        floor = FLOOR_FRAC * nom
        if floor < 0.0:
            floor = 0.0
        if anomaly:
            new_sp = sp - LOWER_STEP
            if new_sp < floor:
                new_sp = floor
            return new_sp
        if sp < nom:
            new_sp = sp + RESTORE_STEP
            if new_sp > nom:
                new_sp = nom
            return new_sp
        return sp

    new_setpoint1 = next_setpoint(setpoint1, nominal1, t1_anomaly)
    new_setpoint2 = next_setpoint(setpoint2, nominal2, t2_anomaly)

    if t1_anomaly:
        diag1 = "tank1 anomaly (" + ", ".join(t1_reasons) + "); demand backed off"
    elif setpoint1 < nominal1:
        diag1 = "tank1 healthy; restoring toward nominal"
    else:
        diag1 = "tank1 nominal"

    if t2_anomaly:
        diag2 = "tank2 anomaly (" + ", ".join(t2_reasons) + "); demand backed off"
    elif setpoint2 < nominal2:
        diag2 = "tank2 healthy; restoring toward nominal"
    else:
        diag2 = "tank2 nominal"

    if t1_anomaly and t2_anomaly:
        coupling = "both tanks flagged on their own evidence (upstream margin applied to tank2)"
    elif t1_anomaly:
        coupling = "tank1 fault present; tank2 disturbance treated as upstream, not flagged"
    elif t2_anomaly:
        coupling = "tank2 fault present with tank1 healthy (no upstream contribution)"
    else:
        coupling = "no cross-tank coupling active"

    return {
        "diagnosis": diag1 + "; " + diag2 + "; " + coupling,
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": t1_anomaly, "tank2": t2_anomaly},
    }
