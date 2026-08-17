"""Thin client for the Colab executor tunnel.

URL and token come from W2P_COLAB_URL / W2P_COLAB_TOKEN. They are
per-session credentials for someone else's runtime and must not be
committed - a tunnel URL in git is a live door left open.
"""
import base64, json, os, time, urllib.request

URL = os.environ.get("W2P_COLAB_URL", "")
TOK = os.environ.get("W2P_COLAB_TOKEN", "")

def _req(path, payload=None, timeout=300):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(URL + path, data=data,
        headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)

def status():
    return _req("/status")

def sh(cmd, timeout=600):
    j = _req("/exec", {"cmd": ["bash", "-lc", cmd], "cwd": "/content", "timeout_s": timeout}, 120)
    jid = j.get("job_id")
    t0 = time.time()
    while time.time() - t0 < timeout + 60:
        s = _req("/jobs/%s" % jid, timeout=60)
        if s.get("state") in ("done", "error", "failed", "finished"):
            # this executor reports state+exit_code but no captured stdout, so
            # commands that need output must redirect to a file and be read back
            return s
        time.sleep(2)
    return {"state": "timeout"}

def put(remote, local):
    with open(local, "rb") as fh:
        data = fh.read()
    _req("/write", {"path": remote, "content": base64.b64encode(data).decode(),
                    "base64": True}, timeout=900)
    return len(data)


def read(remote, timeout=300):
    """Read a remote file back as bytes. /read is a GET with a query param."""
    import base64 as _b, urllib.parse as _up
    j = _req("/read?path=" + _up.quote(remote), timeout=timeout)
    c = j.get("content", "")
    return _b.b64decode(c) if j.get("base64") else c.encode()


def shout(cmd, timeout=600):
    """Run a command and return its combined output."""
    tmpf = "/content/_out_%d.txt" % int(time.time() * 1000)
    r = sh("( %s ) > %s 2>&1" % (cmd, tmpf), timeout)
    try:
        return r, read(tmpf).decode("utf-8", "replace")
    except Exception as exc:
        return r, "(no output: %s)" % exc
