# Two-Tank MIMO Supervisor Training Report

Generated: 2026-09-23T12:42:39+00:00

## Current champion

- Source hash: `f20ace921ce2`
- Dev battery score: 8720.00
- Held-out validation battery score: 13744.17
- Dev/validation gap: +5024.18 (worse on held-out - possible overfitting)

## Trial history

- Total trials logged: 48
  - ROLLBACK: 32
  - REJECTED_REGRESSION: 9
  - PROMOTED: 7

## Score trajectory (promoted trials only)

| Trial | Dev score | Validation score |
|---|---|---|
| gen_2 | 25626.26 | - |
| gen_11 | 16250.65 | - |
| gen_12 | 14154.75 | - |
| gen_14 | 9664.95 | - |
| gen_16 | 9649.99 | - |
| gen_19 | 9585.23 | - |
| gen_42 | 8720.00 | - |

## Known open issues / lessons learned so far

- Requiring both pump-effort elevation and large instantaneous error misses compensated leaks because integral control can drive error near zero while pump effort remains high; persistence of pump effort above a healthy baseline is a stronger leak signal.
- Suppressing tank2 solely on tank1 evidence hides simultaneous tank2 faults; tank2 should only be suppressed when tank1 is active and tank2 lacks an independent strong effort-shift signature.
- Using a low quantile of a window that is mostly fault-filled as the recovery baseline is unreliable; combining it with an early-window/low window baseline and absolute healthy caps improves recovery timing.
- When a setpoint is deliberately depressed after a suspected fault, low pump effort alone is not evidence of fault clearance; the anomaly flag must remain latched until the setpoint has been restored and effort stays low there.
- Using the median of recent effort and a sustained fraction of high-effort samples without an error requirement detects compensated leaks that integral control masks while remaining robust to isolated noise spikes.
- A full-window low-quantile healthy baseline becomes elevated when a fault occupies most of the telemetry window, which silently hides severe persistent faults; using an early-window percentile as the reference keeps the baseline anchored to the pre-fault operating point.
- When tank2 raw detection is made more sensitive, tank2 false positives in upstream-only scenarios are avoided by keeping the independent-override threshold well above the expected coupling disturbance, not by desensitizing tank2 overall.
- An effort-only signal (ignoring error) catches compensated leaks, but it also makes upstream coupling look more like a tank2 fault; therefore cascade suppression must use an independent-override bar that is either error-gated or extremely high relative/absolute to keep tank2 false positives low while still revealing simultaneous tank2 faults.
- A linear regression of downstream pump effort on upstream pump effort over a pre-fault window can estimate expected cascade coupling and isolate a simultaneous downstream fault, because subtracting the expected coupling reduces upstream-only false positives.
- When a setpoint is deliberately depressed, an anomaly detector can approximate latching by treating the depressed setpoint itself as a reason to hold the flag until a longer-than-normal healthy telemetry window is observed; this prevents masked faults from being cleared and reduces missed anomalies.
- Using different window lengths for detection and recovery (a shorter recovery window) reduces post-fault false positives, because long detection windows retain stale high-effort samples after the fault has cleared and keep the raw anomaly signal high.
- Using a median-residual intercept after regressing downstream pump effort on upstream pump effort makes cascade-coupling residuals more robust to a few early contaminated samples; this keeps residuals near zero for upstream-only coupling and makes a simultaneous downstream fault appear as a positive residual.
- When a leak may occupy the entire telemetry window, an absolute effort sanity cap is still needed in parallel with deviation-from-baseline tests; otherwise both early and full-window quantile baselines can become elevated and hide a moderately high but abnormal persistent effort.
- In this scoring setup missed-anomaly penalties dominate same-tank false-positive penalties, so a supervisor should prefer slightly more sensitive detection on tanks that are expected to fault, while keeping never-faulting tank false positives near zero because their FP penalty is much higher.
- Switching to a shorter recent-effort window when the active setpoint is depressed (a per-call proxy for prior anomaly state) reduces post-fault false positives without needing persistent memory, because stale high samples leave the median sooner.
- Using Theil-Sen pairwise median slopes for downstream-on-upstream regression makes the coupling residual insensitive to a few early contaminated samples, allowing residual-based independent tank2 detection to remain usable even when an upstream fault begins near the start of the telemetry window.
- Restoring a depressed setpoint in one large step while keeping the anomaly flag true for that call closes the depressed-setpoint latching gap without persistent state, because a still-present fault will be re-detected at nominal setpoint on the next call.
