def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_setpoint = float(active_setpoints["tank1"])
    tank2_setpoint = float(active_setpoints["tank2"])
    nominal1 = float(nominal_targets["tank1"])
    nominal2 = float(nominal_targets["tank2"])

    if not telemetry_window:
        return {
            "diagnosis": "no telemetry: holding current setpoints",
            "adjusted_setpoints": {"tank1": tank1_setpoint, "tank2": tank2_setpoint},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    eff1 = []
    err1 = []
    aerr1 = []
    eff2 = []
    err2 = []
    aerr2 = []
    for step in telemetry_window:
        t1 = step["tank1"]
        t2 = step["tank2"]
        e1 = float(t1["pump_effort"])
        s1 = float(t1["error"])
        e2 = float(t2["pump_effort"])
        s2 = float(t2["error"])
        eff1.append(e1)
        err1.append(s1)
        aerr1.append(abs(s1))
        eff2.append(e2)
        err2.append(s2)
        aerr2.append(abs(s2))

    n = len(telemetry_window)
    k_r = 12 if n > 12 else n
    k_w = 60 if n > 60 else n

    # Tank 1 is the source tank pumping against its own gravity drain; tank 2 is
    # partly fed by that drain and therefore lives with a wider healthy
    # effort/error band, so the two tanks get different bands.
    CFG1 = {"eff_mild": 2.3, "eff_high": 2.9, "err_low": 0.18, "err_mild": 0.28, "err_high": 0.45}
    CFG2 = {"eff_mild": 2.4, "eff_high": 3.0, "err_low": 0.20, "err_mild": 0.35, "err_high": 0.55}

    def evaluate(eff, err, aerr, cfg, strict):
        e_r = eff[-k_r:]
        a_r = aerr[-k_r:]
        s_r = err[-k_r:]
        e_w = eff[-k_w:]
        a_w = aerr[-k_w:]
        s_w = err[-k_w:]

        m_eff_r = sum(e_r) / float(k_r)
        m_a_r = sum(a_r) / float(k_r)
        m_s_r = sum(s_r) / float(k_r)
        if m_a_r > 1e-9:
            bias_r = abs(m_s_r) / m_a_r
        else:
            bias_r = 0.0
        if m_s_r >= 0.0:
            sgn_r = 1.0
        else:
            sgn_r = -1.0
        pers_r = 0.0
        for i in range(k_r):
            if a_r[i] > cfg["err_mild"]:
                if (1.0 if s_r[i] >= 0.0 else -1.0) == sgn_r:
                    pers_r += 1.0
        pers_r = pers_r / float(k_r)

        m_eff_w = sum(e_w) / float(k_w)
        m_a_w = sum(a_w) / float(k_w)
        m_s_w = sum(s_w) / float(k_w)
        if m_a_w > 1e-9:
            bias_w = abs(m_s_w) / m_a_w
        else:
            bias_w = 0.0
        if m_s_w >= 0.0:
            sgn_w = 1.0
        else:
            sgn_w = -1.0
        pers_w = 0.0
        for i in range(k_w):
            if a_w[i] > cfg["err_mild"]:
                if (1.0 if s_w[i] >= 0.0 else -1.0) == sgn_w:
                    pers_w += 1.0
        pers_w = pers_w / float(k_w)

        k_e = k_w // 2
        if k_e > 0:
            early = aerr[0:k_e]
            m_a_e = sum(early) / float(len(early))
        else:
            m_a_e = m_a_w

        if strict:
            eff_mild = cfg["eff_mild"] + 0.25
            eff_high = cfg["eff_high"] + 0.25
            err_mild = cfg["err_mild"] + 0.06
            err_high = cfg["err_high"] + 0.06
        else:
            eff_mild = cfg["eff_mild"]
            eff_high = cfg["eff_high"]
            err_mild = cfg["err_mild"]
            err_high = cfg["err_high"]

        strong = []
        weak = []

        if m_a_r > err_high and bias_r > 0.40:
            strong.append("systematic tracking error above the healthy band")
        if m_a_r > err_high and pers_r >= 0.70:
            strong.append("large same-sign tracking error persists in recent window")
        if m_a_w > err_high * 1.1 and bias_w > 0.35 and pers_w >= 0.50:
            strong.append("biased error sustained across the full window")
        if m_eff_r > eff_high and pers_r >= 0.50:
            strong.append("pump effort above healthy band with persistent error sign")
        if m_eff_r > eff_high and m_a_r > err_mild * 1.2:
            strong.append("pump effort saturated while residual error remains")
        if m_eff_w > eff_high and m_a_w > cfg["err_low"] and pers_w >= 0.55:
            strong.append("elevated effort and residual error across window")

        if m_eff_r > eff_mild and pers_r >= 0.65:
            weak.append("effort elevated with persistent same-sign error")
        if m_a_r > err_mild and bias_r > 0.50 and m_eff_r > eff_mild * 0.95:
            weak.append("biased residual error with elevated effort")
        if m_a_r > 1.8 * m_a_e and m_a_r > err_mild and m_eff_r > eff_mild:
            weak.append("error rising relative to the start of the window")
        if m_a_w > cfg["err_mild"] and m_eff_w > cfg["eff_mild"] and bias_w > 0.45:
            weak.append("window-wide biased error with coupled effort")

        if strict:
            anomaly = len(strong) > 0
        else:
            anomaly = (len(strong) > 0) or (len(weak) > 0)
        strong_anomaly = len(strong) > 0

        calm = (m_eff_r < cfg["eff_mild"] * 0.9 and m_a_r < cfg["err_mild"] * 0.55
                and pers_r < 0.20 and m_a_w < cfg["err_mild"] * 0.8)

        reasons = []
        for r in strong:
            reasons.append(r)
        for r in weak:
            reasons.append(r)
        return anomaly, strong_anomaly, calm, reasons

    t1_anomaly, t1_strong, t1_calm, t1_reasons = evaluate(eff1, err1, aerr1, CFG1, False)

    # Cascade coordination: tank 1 drains into tank 2, so a genuine tank-1 fault
    # perturbs tank 2 without tank 2 being faulty. While tank 1 has STRONG
    # evidence, tank 2 is evaluated in strict mode (only its own strong evidence
    # can flag it), so upstream-driven disturbances are not charged as tank-2
    # faults while a simultaneous tank-2 fault is still detected.
    if t1_strong:
        t2_anomaly, t2_strong, t2_calm, t2_reasons = evaluate(eff2, err2, aerr2, CFG2, True)
    else:
        t2_anomaly, t2_strong, t2_calm, t2_reasons = evaluate(eff2, err2, aerr2, CFG2, False)

    LOWER_STEP = 0.25

    def next_setpoint(sp, nom, anomaly, calm):
        floor = 0.8 * nom
        if floor < 0.0:
            floor = 0.0
        if anomaly:
            nsp = sp - LOWER_STEP
            if nsp < floor:
                nsp = floor
            return nsp
        if sp < nom:
            gap = nom - sp
            if calm:
                step = 0.35 * gap
                if step < 0.15:
                    step = 0.15
            else:
                step = 0.15 * gap
                if step < 0.05:
                    step = 0.05
            nsp = sp + step
            if nsp > nom:
                nsp = nom
            return nsp
        return sp

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly, t1_calm)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly, t2_calm)

    if t1_anomaly:
        diag1 = "tank1 anomaly (" + ", ".join(t1_reasons) + "); setpoint backed off"
    elif tank1_setpoint < nominal1 and t1_calm:
        diag1 = "tank1 healthy; restoring toward nominal"
    elif tank1_setpoint < nominal1:
        diag1 = "tank1 quiet; restoring gradually toward nominal"
    else:
        diag1 = "tank1 nominal"

    if t2_anomaly:
        diag2 = "tank2 anomaly (" + ", ".join(t2_reasons) + "); setpoint backed off"
    elif tank2_setpoint < nominal2 and t2_calm:
        diag2 = "tank2 healthy; restoring toward nominal"
    elif tank2_setpoint < nominal2:
        diag2 = "tank2 quiet; restoring gradually toward nominal"
    else:
        diag2 = "tank2 nominal"

    if t1_anomaly and t2_anomaly:
        coupling = "both tanks show independent fault evidence; separate back-off applied"
    elif t1_strong and not t2_anomaly:
        coupling = "strong tank1 fault; tank2 disturbance attributed to upstream cascade and not flagged"
    elif t1_anomaly and not t2_anomaly:
        coupling = "mild tank1 anomaly; tank2 required to show independent evidence before flagging"
    elif t2_anomaly:
        coupling = "tank2 fault with tank1 healthy (no upstream contribution)"
    else:
        coupling = "no cross-tank coupling active"

    return {
        "diagnosis": diag1 + "; " + diag2 + "; " + coupling,
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": t1_anomaly, "tank2": t2_anomaly},
    }
