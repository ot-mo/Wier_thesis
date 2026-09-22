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

    eff1 = []
    eff2 = []
    err1 = []
    err2 = []
    for step in telemetry_window:
        eff1.append(float(step['tank1']['pump_effort']))
        eff2.append(float(step['tank2']['pump_effort']))
        err1.append(abs(float(step['tank1']['error'])))
        err2.append(abs(float(step['tank2']['error'])))

    n = len(telemetry_window)

    def median(values):
        ordered = sorted(values)
        m = len(ordered)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return ordered[m // 2]
        return 0.5 * (ordered[m // 2 - 1] + ordered[m // 2])

    # Tank 1 is the upstream source tank: it pumps against its own gravity drain
    # into tank 2, so it holds a higher own-effort band. Tank 2 is partly fed by
    # that drain, so its own-effort band sits lower while its residual tracking
    # error band sits higher (upstream coupling noise). Bands ride on each tank's
    # own observed baseline but are capped, so a fault present from the very
    # start of the window cannot lift its own band out of reach.
    CFG1 = {
        'eff_lo': 3.10, 'eff_delta': 0.70, 'eff_cap': 0.80, 'eff_gap': 0.80,
        'err_lo': 0.46, 'err_delta': 0.22, 'err_cap': 0.26, 'err_gap': 0.32,
        'err_abs': 1.00,
    }
    CFG2 = {
        'eff_lo': 2.85, 'eff_delta': 0.70, 'eff_cap': 0.80, 'eff_gap': 0.80,
        'err_lo': 0.44, 'err_delta': 0.22, 'err_cap': 0.26, 'err_gap': 0.30,
        'err_abs': 0.95,
    }

    def analyse(eff, aerr, cfg, eff_bump, err_bump, err_abs_bump, joint_only):
        k_base = 25 if n > 25 else n
        base_eff = median(eff[:k_base])
        base_err = median(aerr[:k_base])

        eff_lo = cfg['eff_lo']
        if base_eff + cfg['eff_delta'] > eff_lo:
            eff_lo = base_eff + cfg['eff_delta']
        eff_limit = cfg['eff_lo'] + cfg['eff_cap']
        if eff_lo > eff_limit:
            eff_lo = eff_limit
        eff_lo = eff_lo + eff_bump

        err_lo = cfg['err_lo']
        if base_err + cfg['err_delta'] > err_lo:
            err_lo = base_err + cfg['err_delta']
        err_limit = cfg['err_lo'] + cfg['err_cap']
        if err_lo > err_limit:
            err_lo = err_limit
        err_lo = err_lo + err_bump

        eff_hi = eff_lo + cfg['eff_gap']
        err_sev = err_lo + cfg['err_gap']
        err_abs = cfg['err_abs'] + err_abs_bump

        k_fast = 4 if n > 4 else n
        k_mid = 10 if n > 10 else n

        fast_eff = eff[n - k_fast:]
        fast_err = aerr[n - k_fast:]
        mid_eff = eff[n - k_mid:]
        mid_err = aerr[n - k_mid:]

        joint_fast = 0
        sat_fast = 0
        sev_fast = 0
        abs_fast = 0
        for i in range(k_fast):
            if fast_eff[i] > eff_lo and fast_err[i] > err_lo:
                joint_fast = joint_fast + 1
            if fast_eff[i] > eff_hi and fast_err[i] > err_lo:
                sat_fast = sat_fast + 1
            if fast_err[i] > err_sev:
                sev_fast = sev_fast + 1
            if fast_err[i] > err_abs:
                abs_fast = abs_fast + 1

        joint_mid = 0
        for i in range(k_mid):
            if mid_eff[i] > eff_lo and mid_err[i] > err_lo:
                joint_mid = joint_mid + 1

        f_joint_fast = joint_fast / float(k_fast)
        f_sat_fast = sat_fast / float(k_fast)
        f_sev_fast = sev_fast / float(k_fast)
        f_abs_fast = abs_fast / float(k_fast)
        f_joint_mid = joint_mid / float(k_mid)

        mean_eff_mid = sum(mid_eff) / float(k_mid)
        mean_err_mid = sum(mid_err) / float(k_mid)

        reasons = []
        anomaly = False
        if f_joint_fast >= 0.75:
            anomaly = True
            reasons.append('pump effort and tracking error jointly elevated')
        elif f_joint_mid >= 0.6 and mean_eff_mid > eff_lo and mean_err_mid > err_lo:
            anomaly = True
            reasons.append('joint effort/error rise sustained across the window')
        elif f_abs_fast >= 0.5:
            anomaly = True
            reasons.append('tracking error far outside the healthy band')
        elif (not joint_only) and f_sev_fast >= 0.5 and mean_err_mid > err_lo:
            anomaly = True
            reasons.append('sustained error well above the adaptive band')
        elif (not joint_only) and f_sat_fast >= 0.5 and mean_err_mid > 0.8 * err_lo:
            anomaly = True
            reasons.append('pump effort near saturation with residual error')

        calm = (
            (not anomaly)
            and f_joint_mid <= 0.0
            and mean_err_mid < base_err + 0.08
            and mean_eff_mid < base_eff + 0.35
        )
        return anomaly, calm, reasons

    t1_anomaly, t1_calm, t1_reasons = analyse(eff1, err1, CFG1, 0.0, 0.0, 0.0, False)

    # Cascade coordination: tank 1 drains into tank 2, so a tank-1 fault moves
    # tank 2's level and residual error even when tank 2 is healthy. While tank 1
    # is flagged, tank 2 must show its own joint effort+error evidence (an
    # upstream-driven error alone is not accepted), so its single-signal soft
    # rules are switched off and its bands are widened slightly. Its joint rules
    # and its hard absolute error rule stay live, so a genuine simultaneous
    # tank-2 fault is not masked (the old code widened every tank-2 rule and
    # masked those faults).
    if t1_anomaly:
        t2_anomaly, t2_calm, t2_reasons = analyse(eff2, err2, CFG2, 0.25, 0.06, 0.12, True)
    else:
        t2_anomaly, t2_calm, t2_reasons = analyse(eff2, err2, CFG2, 0.0, 0.0, 0.0, False)

    LOWER_STEP = 0.22
    RESTORE_STEP = 0.55

    def next_setpoint(sp, nom, anomaly, calm, hold_restore):
        floor = 0.75 * nom
        if floor < 0.0:
            floor = 0.0
        if anomaly:
            new_sp = sp - LOWER_STEP
            if new_sp < floor:
                new_sp = floor
            return new_sp
        if calm and (not hold_restore) and sp < nom:
            new_sp = sp + RESTORE_STEP
            if new_sp > nom:
                new_sp = nom
            return new_sp
        return sp

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly, t1_calm, False)
    hold2 = t1_anomaly and (not t2_anomaly)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly, t2_calm, hold2)

    if t1_anomaly:
        diag1 = 'tank1 anomaly (' + ', '.join(t1_reasons) + '); demand backed off'
    elif t1_calm and tank1_setpoint < nominal1:
        diag1 = 'tank1 healthy; restoring toward nominal'
    else:
        diag1 = 'tank1 nominal'

    if t2_anomaly:
        diag2 = 'tank2 anomaly (' + ', '.join(t2_reasons) + '); demand backed off'
    elif t2_calm and tank2_setpoint < nominal2:
        diag2 = 'tank2 healthy; restoring toward nominal'
    else:
        diag2 = 'tank2 nominal'

    if t1_anomaly and t2_anomaly:
        coupling = 'both tanks flagged on independent joint evidence'
    elif t1_anomaly:
        coupling = 'tank1 fault active; tank2 disturbance treated as upstream coupling'
    elif t2_anomaly:
        coupling = 'tank2 fault active with tank1 healthy (no upstream contribution)'
    else:
        coupling = 'no cross-tank coupling active'

    return {
        'diagnosis': diag1 + '; ' + diag2 + '; ' + coupling,
        'adjusted_setpoints': {'tank1': new_setpoint1, 'tank2': new_setpoint2},
        'anomaly_flags': {'tank1': t1_anomaly, 'tank2': t2_anomaly},
    }
