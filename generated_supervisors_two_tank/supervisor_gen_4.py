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
    ae1 = []
    ae2 = []
    for step in telemetry_window:
        t1 = step['tank1']
        t2 = step['tank2']
        eff1.append(float(t1['pump_effort']))
        ae1.append(abs(float(t1['error'])))
        eff2.append(float(t2['pump_effort']))
        ae2.append(abs(float(t2['error'])))

    n = len(telemetry_window)

    def pick(sorted_vals, q):
        m = len(sorted_vals)
        if m <= 0:
            return 0.0
        i = int(q * (m - 1))
        if i < 0:
            i = 0
        if i > m - 1:
            i = m - 1
        return sorted_vals[i]

    # Per-tank relative bands. Tank 1 is the upstream source tank pumping
    # against its own gravity drain, so its effort/error signals are the
    # cleaner of the two. Tank 2 is fed largely by tank 1's drain, so its
    # measurements carry the upstream ripple (drift plus extra variance) and
    # it needs wider bands plus an extra confirmation before being flagged.
    CFG1 = {'eff_rel': 0.30, 'eff_abs': 0.08, 'err_rel': 0.55, 'err_abs': 0.03}
    CFG2 = {'eff_rel': 0.45, 'eff_abs': 0.10, 'err_rel': 0.80, 'err_abs': 0.05}

    def detect(eff, aerr, cfg, guard):
        m = len(eff)
        k = 8
        if m < k:
            k = m
        s_eff = sorted(eff)
        s_err = sorted(aerr)

        # Healthy reference = low quantile of the window. Faults only push
        # pump effort and |tracking error| upward, so this stays close to the
        # healthy operating point even when a large slice of the window is
        # faulty, and it self-calibrates to the tank's real effort/error
        # scale instead of trusting a fixed absolute band.
        ref_eff = pick(s_eff, 0.25)
        ref_err = pick(s_err, 0.25)
        med_eff = pick(s_eff, 0.50)
        med_err = pick(s_err, 0.50)

        band_eff = (ref_eff * cfg['eff_rel'] + cfg['eff_abs']) * guard
        band_err = (ref_err * cfg['err_rel'] + cfg['err_abs']) * guard
        eff_hi = ref_eff + band_eff
        err_hi = ref_err + band_err

        r_eff = eff[m - k:]
        r_err = aerr[m - k:]
        rs_eff = sorted(r_eff)
        rs_err = sorted(r_err)
        r_med_eff = pick(rs_eff, 0.5)
        r_med_err = pick(rs_err, 0.5)

        f_eff = 0
        f_err = 0
        f_joint = 0
        for i in range(k):
            e_above = r_eff[i] > eff_hi
            r_above = r_err[i] > err_hi
            if e_above:
                f_eff += 1
            if r_above:
                f_err += 1
            if e_above and r_above:
                f_joint += 1
        f_eff = f_eff / float(k)
        f_err = f_err / float(k)
        f_joint = f_joint / float(k)

        joint_need = 0.50 if guard <= 1.01 else 0.60

        reasons = []
        anomaly = False

        # Joint sustained deviation: both the recent fraction and the recent
        # medians must be above the tank's own band, so single-sample noise
        # spikes or a lone effort excursion cannot raise a flag.
        if f_joint >= joint_need and r_med_eff > eff_hi and r_med_err > err_hi:
            anomaly = True
            reasons.append('effort and |error| jointly above healthy band')

        # Window-level persistence: the deviation must be visible in the bulk
        # of the window as well, not only in the freshest samples.
        if med_eff > eff_hi and med_err > err_hi and f_joint >= 0.30:
            anomaly = True
            reasons.append('sustained deviation from own healthy reference')

        # Off-diagonal faults: an actuator/hydraulic fault that shows on one
        # channel only is still caught, but only when large and persistent.
        if f_eff >= 0.70 and r_med_eff > ref_eff + 2.0 * band_eff:
            anomaly = True
            reasons.append('pump effort far above healthy reference')
        if f_err >= 0.70 and r_med_err > ref_err + 2.0 * band_err:
            anomaly = True
            reasons.append('tracking error far above healthy reference')

        # Safety backstop for a fault that fills the entire window and would
        # otherwise be absorbed into the adaptive reference.
        if med_err > 1.0 or med_eff > 6.0:
            anomaly = True
            reasons.append('gross absolute deviation')

        clear = (not anomaly and f_joint < 0.25 and f_eff < 0.40 and f_err < 0.40
                 and med_eff <= eff_hi and med_err <= err_hi)
        return anomaly, clear, f_joint, reasons

    t1_anomaly, t1_clear, t1_joint, t1_reasons = detect(eff1, ae1, CFG1, 1.0)

    # Cascade coupling: tank 1 drains into tank 2, so a tank-1 fault perturbs
    # tank 2 through the shared flow. While tank 1 is flagged, tank 2 must show
    # a 25% wider deviation and one extra confirmation sample before it is
    # flagged too. A genuinely simultaneous tank-2 fault is large enough to
    # clear that higher bar, so upstream-driven false positives are removed
    # without blinding the supervisor to a real two-tank fault.
    guard2 = 1.25 if t1_anomaly else 1.0
    t2_anomaly, t2_clear, t2_joint, t2_reasons = detect(eff2, ae2, CFG2, guard2)

    LOWER_STEP = 0.2
    RESTORE_STEP = 0.5

    def next_setpoint(sp, nom, anomaly, clear):
        floor = 0.7 * nom
        if floor < 0.0:
            floor = 0.0
        if anomaly:
            new_sp = sp - LOWER_STEP
            if new_sp < floor:
                new_sp = floor
            return new_sp
        if clear and sp < nom:
            new_sp = sp + RESTORE_STEP
            if new_sp > nom:
                new_sp = nom
            return new_sp
        return sp

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly, t1_clear)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly, t2_clear)

    if t1_anomaly:
        diag1 = 'tank1 anomaly (' + ', '.join(t1_reasons) + '); demand backed off'
    elif t1_clear and tank1_setpoint < nominal1:
        diag1 = 'tank1 healthy; restoring toward nominal'
    else:
        diag1 = 'tank1 nominal'

    if t2_anomaly:
        diag2 = 'tank2 anomaly (' + ', '.join(t2_reasons) + '); demand backed off'
    elif t2_clear and tank2_setpoint < nominal2:
        diag2 = 'tank2 healthy; restoring toward nominal'
    else:
        diag2 = 'tank2 nominal'

    if t1_anomaly and t2_anomaly:
        coupling = 'both tanks flagged on independent evidence (cascade bar applied to tank2)'
    elif t1_anomaly:
        coupling = 'tank1 fault present; tank2 must clear a raised cascade bar to be flagged'
    elif t2_anomaly:
        coupling = 'tank2 fault present with tank1 healthy (upstream clean, tank2 independent)'
    else:
        coupling = 'no cross-tank coupling active'

    return {
        'diagnosis': diag1 + '; ' + diag2 + '; ' + coupling,
        'adjusted_setpoints': {'tank1': new_setpoint1, 'tank2': new_setpoint2},
        'anomaly_flags': {'tank1': t1_anomaly, 'tank2': t2_anomaly},
    }
