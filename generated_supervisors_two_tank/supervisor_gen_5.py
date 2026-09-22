def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_setpoint = float(active_setpoints['tank1'])
    tank2_setpoint = float(active_setpoints['tank2'])
    nominal1 = float(nominal_targets['tank1'])
    nominal2 = float(nominal_targets['tank2'])

    if not telemetry_window:
        return {
            'diagnosis': 'no telemetry: holding current setpoints',
            'adjusted_setpoints': {'tank1': tank1_setpoint, 'tank2': tank2_setpoint},
            'anomaly_flags': {'tank1': False, 'tank2': False},
        }

    try:
        n = len(telemetry_window)
        eff1 = []
        eff2 = []
        ae1 = []
        ae2 = []
        for step in telemetry_window:
            s1 = step['tank1']
            s2 = step['tank2']
            eff1.append(float(s1['pump_effort']))
            ae1.append(abs(float(s1['error'])))
            eff2.append(float(s2['pump_effort']))
            ae2.append(abs(float(s2['error'])))
    except Exception:
        return {
            'diagnosis': 'unreadable telemetry: holding current setpoints',
            'adjusted_setpoints': {'tank1': tank1_setpoint, 'tank2': tank2_setpoint},
            'anomaly_flags': {'tank1': False, 'tank2': False},
        }

    k_recent = 12 if n > 12 else n
    if k_recent < 1:
        k_recent = 1

    def quiet_level(vals):
        m = len(vals)
        if m <= 0:
            return 0.0
        bsize = m // 6
        if bsize < 4:
            bsize = 4
        if bsize > m:
            bsize = m
        best = None
        i = 0
        while i < m:
            j = i + bsize
            if j > m:
                j = m
            seg = vals[i:j]
            if len(seg) > 0:
                v = sum(seg) / float(len(seg))
                if best is None or v < best:
                    best = v
            i = j
        if best is None:
            return 0.0
        return best

    def analyse(eff, aerr, err_margin, eff_margin, abs_err, abs_eff, err_bias, eff_bias):
        base_e = quiet_level(eff)
        base_a = quiet_level(aerr)
        if base_a < 0.06:
            base_a = 0.06
        if base_e < 0.05:
            base_e = 0.05

        er = eff[n - k_recent:]
        ar = aerr[n - k_recent:]
        kk = float(k_recent)
        mean_e = sum(er) / kk
        mean_a = sum(ar) / kk

        err_thr = max(base_a * 1.5, base_a + err_margin) + err_bias
        eff_thr = max(base_e * 1.12, base_e + eff_margin) + eff_bias
        strong_thr = 1.8 * base_a + 0.15

        f_rel = sum(1 for v in ar if v > err_thr) / kk
        f_sev = sum(1 for v in ar if v > abs_err) / kk
        f_strong = sum(1 for v in ar if v > strong_thr) / kk

        reasons = []
        if mean_a > err_thr and f_rel >= 0.6:
            reasons.append('tracking error persistently above own healthy band')
        if f_sev >= 0.6:
            reasons.append('large absolute tracking error')
        if mean_a > strong_thr and f_strong >= 0.5:
            reasons.append('tracking error far above own healthy band')
        if mean_e > eff_thr and mean_a > 0.75 * err_thr and f_rel >= 0.4:
            reasons.append('effort and error jointly above healthy band')
        if mean_e > abs_eff and mean_a > base_a + 0.08:
            reasons.append('effort far above healthy band with residual error')

        return len(reasons) > 0, reasons

    t1_anomaly, t1_reasons = analyse(eff1, ae1, 0.12, 0.30, 0.55, 3.6, 0.0, 0.0)

    if t1_anomaly:
        t2_anomaly, t2_reasons = analyse(eff2, ae2, 0.12, 0.30, 0.50, 3.3, 0.05, 0.25)
    else:
        t2_anomaly, t2_reasons = analyse(eff2, ae2, 0.12, 0.30, 0.50, 3.3, 0.0, 0.0)

    LOWER_STEP = 0.2
    RESTORE_STEP = 0.5

    def next_setpoint(sp, nom, anomaly):
        floor = 0.7 * nom
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

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly)

    if t1_anomaly:
        diag1 = 'tank1 anomaly (' + '; '.join(t1_reasons) + '); demand backed off'
    elif tank1_setpoint < nominal1:
        diag1 = 'tank1 clear of fault; restoring toward nominal'
    else:
        diag1 = 'tank1 nominal'

    if t2_anomaly:
        diag2 = 'tank2 anomaly (' + '; '.join(t2_reasons) + '); demand backed off'
    elif tank2_setpoint < nominal2:
        diag2 = 'tank2 clear of fault; restoring toward nominal'
    else:
        diag2 = 'tank2 nominal'

    if t1_anomaly and t2_anomaly:
        coupling = 'both tanks exceed their own healthy bands on independent evidence'
    elif t1_anomaly:
        coupling = 'tank1 fault active; tank2 judged against its own band with a mild cascade allowance'
    elif t2_anomaly:
        coupling = 'tank2 fault with tank1 quiet; not attributable to upstream disturbance'
    else:
        coupling = 'no cross-tank coupling active'

    return {
        'diagnosis': diag1 + '; ' + diag2 + '; ' + coupling,
        'adjusted_setpoints': {'tank1': new_setpoint1, 'tank2': new_setpoint2},
        'anomaly_flags': {'tank1': t1_anomaly, 'tank2': t2_anomaly},
    }
