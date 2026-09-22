"""Adversarial tests for supervisor_security.py. Run: python test_supervisor_security.py"""

import time

from supervisor_security import check_source, call_with_timeout

VALID_SOURCE = """
def supervise(telemetry_window, active_setpoint, nominal_target):
    return {"diagnosis": "ok", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}
"""

MALICIOUS_IMPORT = """
import os

def supervise(telemetry_window, active_setpoint, nominal_target):
    os.system("echo pwned")
    return {"diagnosis": "ok", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}
"""

MALICIOUS_OPEN = """
def supervise(telemetry_window, active_setpoint, nominal_target):
    f = open("secrets.txt", "w")
    f.write("leaked")
    return {"diagnosis": "ok", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}
"""

MALICIOUS_SANDBOX_ESCAPE = """
def supervise(telemetry_window, active_setpoint, nominal_target):
    base = ().__class__.__bases__[0]
    return {"diagnosis": str(base), "adjusted_setpoint": active_setpoint, "anomaly_flag": False}
"""


def test_valid_source_passes():
    ok, reason = check_source(VALID_SOURCE)
    assert ok, f"expected valid source to pass, got rejection: {reason}"
    print("PASS: valid source accepted")


def test_import_rejected():
    ok, reason = check_source(MALICIOUS_IMPORT)
    assert not ok, "expected import to be rejected"
    print(f"PASS: import rejected ({reason})")


def test_open_rejected():
    ok, reason = check_source(MALICIOUS_OPEN)
    assert not ok, "expected open() to be rejected"
    print(f"PASS: open() rejected ({reason})")


def test_sandbox_escape_rejected():
    ok, reason = check_source(MALICIOUS_SANDBOX_ESCAPE)
    assert not ok, "expected dunder sandbox escape to be rejected"
    print(f"PASS: sandbox escape rejected ({reason})")


def test_infinite_loop_times_out():
    def infinite_loop(*args):
        while True:
            pass

    start = time.time()
    result, err = call_with_timeout(infinite_loop, (1, 2, 3), timeout_s=0.3)
    elapsed = time.time() - start
    assert err == "timeout", f"expected timeout, got {err!r}"
    assert elapsed < 2.0, f"timeout took too long: {elapsed}s"
    print(f"PASS: infinite loop caught by timeout in {elapsed:.2f}s")


if __name__ == "__main__":
    test_valid_source_passes()
    test_import_rejected()
    test_open_rejected()
    test_sandbox_escape_rejected()
    test_infinite_loop_times_out()
    print("\nAll security tests passed.")
