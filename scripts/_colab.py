"""Tiny local driver for the agent-colab-direct executor (raw HTTP, stdlib only).

Env: COLAB_URL, COLAB_TOKEN.
  python scripts/_colab.py exec [--cwd DIR] [--timeout S] -- ARGV...
  python scripts/_colab.py put  LOCAL  REMOTE
  python scripts/_colab.py get  REMOTE LOCAL
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = os.environ["COLAB_URL"].rstrip("/")
TOK = os.environ["COLAB_TOKEN"]


def _req(method, path, body=None, params=None, timeout=180):
    url = URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def do_exec(argv, cwd=None, timeout=600, poll=3.0):
    r = _req("POST", "/exec", {"cmd": argv, "cwd": cwd, "timeout_s": timeout})
    jid = r["job_id"]
    print(f"[job {jid}] running: {' '.join(argv)[:120]}")
    t0 = time.time()
    while True:
        time.sleep(poll)
        j = _req("GET", f"/jobs/{jid}")
        if j["state"] != "running":
            print(f"=== state={j['state']} exit={j.get('exit_code')} dur={round(j.get('duration_s') or 0,1)}s")
            print(j.get("log_tail", ""))
            return j
        if time.time() - t0 > timeout + 60:
            print("!! local poll timeout"); return j


def put(local, remote):
    raw = open(local, "rb").read()
    r = _req("POST", "/write", {"path": remote, "content": base64.b64encode(raw).decode(), "base64": True})
    print(f"put {r['bytes_written']}B -> {remote}  sha={r['sha256'][:10]}")


def get(remote, local):
    r = _req("GET", "/read", params={"path": remote, "base64": "true", "max_size_mb": "40"})
    if "content" not in r:
        print("get ERROR:", r); return False
    raw = base64.b64decode(r["content"])
    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
    open(local, "wb").write(raw)
    print(f"get {len(raw)}B -> {local}")
    return True


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0]
    if cmd == "exec":
        cwd, timeout = None, 600
        i = 1
        while i < len(a) and a[i] != "--":
            if a[i] == "--cwd":
                cwd = a[i + 1]; i += 2
            elif a[i] == "--timeout":
                timeout = int(a[i + 1]); i += 2
            else:
                i += 1
        argv = a[i + 1:]
        j = do_exec(argv, cwd=cwd, timeout=timeout)
        sys.exit(0 if j.get("exit_code") == 0 else 1)
    elif cmd == "put":
        put(a[1], a[2])
    elif cmd == "get":
        ok = get(a[1], a[2])
        sys.exit(0 if ok else 1)
    else:
        print("unknown", cmd); sys.exit(2)
