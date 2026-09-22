"""Security gate for LLM-generated Layer-2 supervisor code.

Two independent stages must both pass before untrusted candidate source is ever
called with real data (live or offline evaluation):
  1. check_source() - static AST denylist (no imports, no dunder/attribute
     escapes, no dangerous names, single top-level `supervise` def).
  2. safe_exec_supervisor() - exec with a minimal restricted builtins dict.

call_with_timeout() wraps every actual invocation (definition-time exec AND
each per-cycle call) in a wall-clock timeout, since Windows has no signal.alarm.
Known limitation: a timed-out worker thread cannot be forcibly killed in pure
Python and keeps running in the background. Acceptable here since this project
does not use subprocess isolation.
"""

import ast
import hashlib
import math
import statistics
import threading
from typing import Callable, Optional

MAX_SOURCE_CHARS = 20_000
REQUIRED_FUNC_NAME = "supervise"
REQUIRED_FUNC_ARGS = ("telemetry_window", "active_setpoint", "nominal_target")

BLOCKED_NAMES = {
    "eval", "exec", "compile", "open", "input", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "breakpoint", "help",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
}

BLOCKED_ATTR_PREFIXES = ("__",)
BLOCKED_ATTR_NAMES = {
    "__class__", "__bases__", "__subclasses__", "__globals__", "__builtins__",
    "__import__", "__code__", "__reduce__", "__getattribute__", "__mro__",
    "system", "popen", "remove", "unlink", "rmdir", "environ", "getenv",
}

SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "len": len, "round": round,
    "sum": sum, "sorted": sorted, "range": range, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter,
    "float": float, "int": int, "bool": bool, "str": str, "list": list,
    "dict": dict, "tuple": tuple, "set": set,
    "True": True, "False": False, "None": None,
}
SAFE_GLOBALS_TEMPLATE = {"__builtins__": SAFE_BUILTINS, "math": math, "statistics": statistics}


def check_source(source: str) -> tuple:
    """Static AST check. Returns (ok, reason_if_rejected)."""
    if len(source) > MAX_SOURCE_CHARS:
        return False, f"source too large ({len(source)} chars > {MAX_SOURCE_CHARS})"

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"syntax error: {e}"

    supervise_defs = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == REQUIRED_FUNC_NAME:
                supervise_defs.append(node)
            continue
        if isinstance(node, ast.Assign):
            continue
        return False, f"disallowed top-level statement: {type(node).__name__}"

    if len(supervise_defs) != 1:
        return False, f"expected exactly one top-level def {REQUIRED_FUNC_NAME}(...), found {len(supervise_defs)}"

    fn = supervise_defs[0]
    arg_names = tuple(a.arg for a in fn.args.args)
    if arg_names != REQUIRED_FUNC_ARGS:
        return False, f"def {REQUIRED_FUNC_NAME} must have args {REQUIRED_FUNC_ARGS}, found {arg_names}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "import statements are not allowed"
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return False, "global/nonlocal statements are not allowed"
        if isinstance(node, ast.With):
            return False, "with statements are not allowed"
        if isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith(BLOCKED_ATTR_PREFIXES) or attr in BLOCKED_ATTR_NAMES:
                return False, f"disallowed attribute access: .{attr}"
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            return False, f"disallowed name: {node.id}"

    return True, None


def safe_exec_supervisor(source: str) -> tuple:
    """Executes already-checked source in a restricted namespace.
    Returns (callable_or_None, error_reason_or_None).
    """
    local_ns = {}
    safe_globals = dict(SAFE_GLOBALS_TEMPLATE)
    try:
        code_obj = compile(source, "<candidate_supervisor>", "exec")
        exec(code_obj, safe_globals, local_ns)
    except Exception as e:
        return None, f"exec failed: {e}"

    fn = local_ns.get(REQUIRED_FUNC_NAME)
    if not callable(fn):
        return None, f"no callable '{REQUIRED_FUNC_NAME}' produced by source"
    return fn, None


def call_with_timeout(fn: Callable, args: tuple, timeout_s: float = 0.5) -> tuple:
    """Runs fn(*args) in a daemon worker thread with a wall-clock timeout.
    Returns (result, None) on success or (None, "timeout"|"exception: ...") on failure.

    Uses a raw daemon threading.Thread rather than ThreadPoolExecutor: the
    latter registers its worker threads with concurrent.futures.thread's atexit
    hook, which joins every outstanding worker (even ones abandoned via
    shutdown(wait=False)) before the interpreter can exit - so a single runaway
    candidate (e.g. `while True: pass`) would hang process exit forever. A
    daemon thread is not joined at exit, so the caller returns promptly and the
    process can still exit; the stuck thread itself cannot be forcibly killed
    in pure Python and keeps running in the background - an accepted limitation
    given the no-subprocess-isolation design choice.
    """
    box = {}

    def runner():
        try:
            box["result"] = fn(*args)
        except Exception as e:
            box["error"] = f"exception: {e}"

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout_s)

    if thread.is_alive():
        return None, "timeout"
    if "error" in box:
        return None, box["error"]
    return box.get("result"), None


def load_supervisor(path: str, timeout_s: float = 2.0) -> tuple:
    """Reads source from `path`, runs check_source, then safe_exec_supervisor.

    Returns (fn, None) on success or (None, rejection_reason) on failure. `fn` is
    the raw, unguarded `supervise` callable with a `.source_hash` (sha256 of the
    source) attribute attached; callers (tank_sim.run_episode) are responsible for
    wrapping each invocation in call_with_timeout so failures are logged as typed
    failure points rather than silently swallowed here.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return None, f"could not read supervisor file: {e}"

    ok, reason = check_source(source)
    if not ok:
        return None, f"security check rejected: {reason}"

    def _load():
        fn, err = safe_exec_supervisor(source)
        if err:
            raise RuntimeError(err)
        return fn

    fn, err = call_with_timeout(_load, (), timeout_s=timeout_s)
    if err is not None:
        return None, f"failed to load supervisor: {err}"

    fn.source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return fn, None
