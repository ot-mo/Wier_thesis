def supervise(telemetry_window, active_setpoint, nominal_target):
    try:
        sp = float(active_setpoint)
    except Exception:
        sp = 0.0
    if sp != sp or sp > 1e18 or sp < -1e18:
        sp = 0.0
    try:
        nt = float(nominal_target)
    except Exception:
        nt = 0.0
    if nt != nt or nt > 1e18 or nt < -1e18:
        nt = 0.0

    try:
        n = len(telemetry_window)
    except Exception:
        n = 0
    if n == 0:
        return {'diagnosis': 'No telemetry data.', 'adjusted_setpoint': sp, 'anomaly_flag': False}

    try:
        efforts = []
        errors = []
        for step in telemetry_window:
            ev = 0.0
            er = 0.0
            try:
                ev = float(step.get('pump_effort', 0.0))
            except Exception:
                ev = 0.0
            try:
                er = float(step.get('error', 0.0))
            except Exception:
                er = 0.0
            if ev != ev or ev > 1e18 or ev < -1e18:
                ev = 0.0
            if er != er or er > 1e18 or er < -1e18:
                er = 0.0
            efforts.append(ev)
            errors.append(er)

        def _median(vals):
            if not vals:
                return 0.0
            sv = sorted(vals)
            m = len(sv)
            if m % 2 == 1:
                return sv[m // 2]
            return 0.5 * (sv[m // 2 - 1] + sv[m // 2])

        def _slope(vals):
            m = len(vals)
            if m < 3:
                return 0.0
            mx = (m - 1) / 2.0
            my = sum(vals) / float(m)
            cov = 0.0
            var = 0.0
            for i in range(m):
                dx = i - mx
                cov += dx * (vals[i] - my)
                var += dx * dx
            if var <= 1e-9:
                return 0.0
            return cov / var

        short_n = min(6, n)
        long_n = min(24, n)
        eff_short = efforts[-short_n:]
        eff_long = efforts[-long_n:]
        err_short = errors[-short_n:]
        err_long = errors[-long_n:]

        # ----- model of the nominal pump effort for this setpoint -----
        if nt > 0.0:
            ratio = sp / nt
            if ratio < 0.1:
                ratio = 0.1
            elif ratio > 1.5:
                ratio = 1.5
            base_effort = 0.923 * (ratio ** 0.5)
        else:
            base_effort = 0.923

        # ----- residual (extra load) evidence -------------------------
        res_short = [e - base_effort for e in eff_short]
        res_long = [e - base_effort for e in eff_long]

        med_res_short = _median(res_short)
        med_res_long = _median(res_long)
        mean_res_short = sum(res_short) / float(len(res_short))
        mean_res_long = sum(res_long) / float(len(res_long))
        last_res = res_short[-1]
        med_err_short = _median(err_short)

        # ----- robust, capped noise scale -----------------------------
        devs = [abs(x - med_res_short) for x in res_short]
        sigma = 1.4826 * _median(devs)
        if sigma < 0.012:
            sigma = 0.012
        if sigma > 0.20:
            sigma = 0.20

        t_mild = max(0.065, 3.0 * sigma)
        if t_mild > 0.30:
            t_mild = 0.30
        t_moderate = max(0.16, 5.0 * sigma)
        if t_moderate > 0.60:
            t_moderate = 0.60
        t_severe = max(0.40, 8.0 * sigma)
        if t_severe > 1.20:
            t_severe = 1.20

        short_over_mild = 0
        short_over_mod = 0
        for x in res_short:
            if x > t_mild:
                short_over_mild += 1
            if x > t_moderate:
                short_over_mod += 1
        long_over_mild = 0
        for x in res_long:
            if x > t_mild:
                long_over_mild += 1

        eff_slope_short = _slope(eff_short)

        # ----- transient (level catch-up / recovery) discrimination ---
        # Sign-agnostic: uses |error|. A shrinking tracking error while the
        # pump is not ramping upward means the loop is catching up, not that
        # a new sustained load exists.
        catching_up = False
        if short_n >= 4:
            e0 = abs(err_short[0])
            e1 = abs(err_short[-1])
            if e0 > 0.08 and e1 < 0.80 * e0 and eff_slope_short < 0.02:
                catching_up = True

        # ----- anomaly conditions (each needs persistence) ------------
        severe_excess = (
            short_n >= 3 and
            med_res_short > t_severe and
            last_res > t_moderate
        )

        moderate_excess = (
            short_n >= 4 and
            med_res_short > t_moderate and
            short_over_mod >= 2 and
            mean_res_short > t_moderate * 0.8
        )

        persistent_excess = (
            long_n >= 10 and
            med_res_long > t_mild and
            mean_res_long > t_mild * 0.8 and
            long_over_mild >= max(6, int(0.6 * long_n))
        )

        rising_excess = (
            short_n >= 4 and
            last_res > t_mild and
            eff_slope_short > 0.015 and
            short_over_mild >= 2
        )

        steady_mild = (
            n >= 12 and
            med_res_long > 0.05 and
            mean_res_long > 0.06 and
            med_res_short > 0.04 and
            long_over_mild >= max(5, int(0.55 * long_n))
        )

        anomaly_flag = bool(
            severe_excess or moderate_excess or persistent_excess or
            rising_excess or steady_mild
        )

        if catching_up:
            anomaly_flag = False

        # ----- strict calm release (hysteresis) -----------------------
        if (short_n >= 4 and
                short_over_mild == 0 and
                abs(med_res_short) < 0.5 * t_mild and
                abs(med_err_short) < 0.06):
            anomaly_flag = False

        if n < 3:
            anomaly_flag = False

        # ----- actuation ----------------------------------------------
        if anomaly_flag:
            if med_res_short > 0.50:
                adjustment = 0.25
            elif med_res_short > 0.25:
                adjustment = 0.15
            elif med_res_short > 0.10:
                adjustment = 0.08
            else:
                adjustment = 0.04
            adjusted_setpoint = sp - adjustment
            if adjusted_setpoint < 0.0:
                adjusted_setpoint = 0.0
            diagnosis = ('Extra load detected: median effort excess %.2f over expected %.2f; '
                         'lowering setpoint by %.2f.' % (med_res_short, base_effort, adjustment))
        else:
            gap = nt - sp
            if (sp < nt and gap > 1e-9 and short_over_mild == 0 and
                    med_res_short < 0.05 and abs(med_err_short) < 0.07):
                step_r = gap * 0.35
                if step_r > 0.15:
                    step_r = 0.15
                if step_r < 0.01:
                    step_r = gap
                adjusted_setpoint = sp + step_r
                if adjusted_setpoint > nt:
                    adjusted_setpoint = nt
                diagnosis = ('System stable and below nominal; restoring setpoint toward '
                             'nominal target (gap %.2f).' % gap)
            else:
                adjusted_setpoint = sp
                diagnosis = 'Nominal operation, no action.'

        return {'diagnosis': diagnosis,
                'adjusted_setpoint': float(adjusted_setpoint),
                'anomaly_flag': bool(anomaly_flag)}
    except Exception:
        return {'diagnosis': 'Fallback: telemetry could not be processed; holding setpoint.',
                'adjusted_setpoint': sp,
                'anomaly_flag': False}
