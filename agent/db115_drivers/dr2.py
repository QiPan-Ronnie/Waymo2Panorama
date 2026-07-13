"""Dual-runtime dr_run wrapper: A100 (active_url_5.json, default) + L4
(active_url_l4.json). dr_run.py hardcodes the secret file, so the L4 variant
is loaded from patched source. get(gpu) -> module with _exec/dr_launch/
dr_wait/dr_pull bound to that runtime."""
import os
import sys
import types

OLD_SP = r"C:\Users\14294\AppData\Local\Temp\claude\D--BaiduSyncdisk-2024-to-future-koi-chen\c7c4396c-8d95-4a4e-9965-ee96b7f064a6\scratchpad"
_cache = {}


def get(gpu="a100"):
    if gpu in _cache:
        return _cache[gpu]
    src = open(os.path.join(OLD_SP, "dr_run.py"), encoding="utf-8-sig").read()
    if gpu != "a100":
        src = src.replace("active_url_5.json", "active_url_%s.json" % gpu)
    mod = types.ModuleType("dr_run_" + gpu)
    mod.__file__ = os.path.join(OLD_SP, "dr_run_" + gpu + ".py")
    sys.path.insert(0, OLD_SP)
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    # env-per-call guard: dr_run sets the secret file at import, and the two
    # variants share one process env — re-pin it before every call
    secret = os.path.expanduser("~/.waymo2panorama/runtime/"
                                + ("active_url_5.json" if gpu == "a100" else "active_url_%s.json" % gpu))
    for fn in ["_exec", "dr_launch", "dr_wait", "dr_pull"]:
        orig = mod.__dict__[fn]

        def _make(orig=orig, secret=secret):
            def wrapped(*a, **k):
                os.environ["W2P_RUNTIME_SECRET_FILE"] = secret
                return orig(*a, **k)
            return wrapped
        mod.__dict__[fn] = _make()
    sys.modules["dr_run_" + gpu] = mod
    _cache[gpu] = mod
    return mod
