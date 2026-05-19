# W2P-001 — `colab-mcp` `open_colab_browser_connection` opens new empty notebook

**Status**: **PATCHED.** Forked + fixed at <https://github.com/QiPan-Ronnie/colab-mcp> (commit `aa6ad7f`, 2026-05-19). Local install also live at `koi chen/tools/colab-mcp/`. Claude Code MCP config switched to local fork. See "Resolution" section at bottom.
**Date discovered**: 2026-05-16
**Date patched**: 2026-05-19
**Discovered during**: Phase 0.5 spike Cell 6 → Cell 7 (when re-establishing MCP connection after losing it briefly).

## Symptom

The agent calls `mcp__colab-mcp__open_colab_browser_connection`. It returns `{"result": true}`. Then `get_cells()` returns only one empty cell (id `lIYdn1woOS1n`), not the 10 cells the user has in their real notebook tab.

This is reliably reproducible: every time the tool is invoked, it binds to an empty notebook rather than the user's focused tab. Once bound, the user's real notebook cannot displace it.

## Root cause (from source inspection, agent run a497d6217f0020610)

Two interacting issues in `googlecolab/colab-mcp`:

1. **`src/colab_mcp/session.py` → `check_session_proxy_tool_fn`** (the function wired up as `open_colab_browser_connection`):

   ```python
   async def check_session_proxy_tool_fn(ctx: Context = CurrentContext()) -> bool:
       fe_connected = ctx.get_state(FE_CONNECTED_KEY)
       token = ctx.get_state(PROXY_TOKEN_KEY)
       port  = ctx.get_state(PROXY_PORT_KEY)
       if fe_connected:
           return True
       webbrowser.open_new(
           f"{COLAB}{SCRATCH_PATH}#mcpProxyToken={token}&mcpProxyPort={port}"
       )
       return False
   ```

   `SCRATCH_PATH = "/notebooks/empty.ipynb"` is hard-coded in `websocket_server.py`. The tool **unconditionally opens a new empty Colab notebook** and never inspects which tab the user has focused. The Python MCP server has no browser-tab discovery mechanism — it can't see what tabs exist.

2. **Single-connection lock** in `ColabWebSocketServer`: only one client may hold `connection_lock` at a time. Once the freshly-opened empty notebook connects and grabs the lock, any subsequent attempt (including the user's real notebook reloading with the same fragment) is rejected with WebSocket close code 1013 ("Server is busy").

The token/port pair is generated **once at MCP server startup** (`secrets.token_urlsafe(16)`) and reused for the entire server lifetime.

## Why the first call seemed to work

Earlier in the same session, calling `open_colab_browser_connection` opened that initial scratch tab — and we then injected our 10 cells INTO that scratch tab. We mistakenly assumed it had bound to a different notebook. Actually it had always been the scratch tab; the scratch tab had simply received all the cells we added.

When the connection was later lost (e.g. the tab focus changed, the websocket dropped, or `open_colab_browser_connection` was called again with a stale state), the second call opened **another** fresh empty scratch tab and grabbed the lock. The first scratch tab (with all our work) was left orphaned.

## Workaround (no source change required)

**Rule**: after the first successful binding, **never call `open_colab_browser_connection` again** in the session. All other cell-op tools (`get_cells`, `add_code_cell`, `run_code_cell`, `update_cell`, …) route through the existing connection via `ColabProxyMiddleware.on_message` and do NOT trigger the buggy open path.

**If the connection is lost** (e.g. user closed the tab, kernel disconnect, MCP server restarted):
1. Treat the work in that tab as the canonical record (it survives — the .ipynb is autosaved by Colab).
2. To re-attach an agent to that notebook:
   a. Kill the local colab-mcp process so the `connection_lock` is released and a new token/port are generated on restart.
   b. Start the MCP server fresh and read its log `colab-mcp.<timestamp>.log` (Windows: `%TEMP%\colab-mcp`) for the line `Starting WebSocket server on ws://localhost:<port>` and the token used in the URL it opens.
   c. In the user's existing Colab tab, edit the URL bar: append `#mcpProxyToken=<TOKEN>&mcpProxyPort=<PORT>` and reload that tab.
   d. The real tab grabs the lock first. Now invoke any cell-op tool. The middleware sees `fe_connected=True` and skips the scratch-tab branch entirely.
3. Pragmatic alternative: don't fight it — keep using the originally-bound scratch tab. Output value (mosaic.png, probe_log.txt) is on Drive anyway via Cell 8.

**For Waymo2Panorama specifically**: every Colab session, **call `open_colab_browser_connection` exactly once** at session start, run all subsequent operations on the resulting scratch tab. Sync important artifacts to Drive via Cell 8 pattern so the tab is disposable.

## Upstream patch sketch

The cleanest fix is a two-line change in `session.py`:

```python
async def check_session_proxy_tool_fn(
    notebook_url: str | None = None,
    ctx: Context = CurrentContext(),
) -> bool:
    if ctx.get_state(FE_CONNECTED_KEY):
        return True
    token = ctx.get_state(PROXY_TOKEN_KEY)
    port  = ctx.get_state(PROXY_PORT_KEY)
    fragment = f"#mcpProxyToken={token}&mcpProxyPort={port}"
    if notebook_url:
        webbrowser.open_new(notebook_url + fragment)
    else:
        # Surface the fragment so the user can paste it onto their already-focused tab.
        raise RuntimeError(
            f"Not connected. Append this to your current Colab tab URL and reload: {fragment}"
        )
    return False
```

This makes the tool:
- Accept an optional `notebook_url` arg to open a specific notebook;
- When called with no arg, return the fragment so the user can paste it onto whichever tab they already have open instead of forcing a scratch tab.

## Upstream issue status

Scanned `googlecolab/colab-mcp` issues #76–#89 (full current list at time of investigation): **no existing issue** mentions multi-tab, wrong notebook, empty notebook, re-bind, focus, or `empty.ipynb`. Closest unrelated: #83 (Claude Desktop on Windows 11 tool visibility).

**Action item (optional)**: file an issue at `googlecolab/colab-mcp` linking to this diagnosis and the patch sketch. Not blocking for Waymo2Panorama.

## Operational consequence for this project

- Phase 0.5 spike completed successfully via the originally-bound scratch tab; outputs are on Drive.
- For future Colab work, the **handoff template in `agent/agent-roster.md`** must include the rule: "call `open_colab_browser_connection` exactly once per session, never re-invoke."
- Added to `plan.md` §6 risk register as W2P-001.
- This document remains the canonical reference.

## Cross-references

- Source files investigated (all on GitHub, googlecolab/colab-mcp):
  - `src/colab_mcp/session.py` (the bug)
  - `src/colab_mcp/websocket_server.py` (single connection_lock, SCRATCH_PATH)
  - `src/colab_mcp/__init__.py` (wiring)
- Pi3 phase MCP handoff: `../../../01-pi3/agent/pi3_handoff.md` §4.2 (mentions dynamic-tools registration; doesn't mention this bug because Pi3 phase used only one Colab tab session and never re-invoked).

---

## Resolution (2026-05-19)

Cloned upstream `googlecolab/colab-mcp` (at upstream commit `b9ab389`) to
`koi chen/tools/colab-mcp/` and applied the minimal patch sketched above to
`src/colab_mcp/session.py`. Patch is committed as `aa6ad7f` in our fork at
<https://github.com/QiPan-Ronnie/colab-mcp>.

### Patched behavior

`open_colab_browser_connection` now accepts an optional `notebook_url: str | None = None`:

| Call form | Behavior |
|---|---|
| `open_colab_browser_connection()` (no arg) | **No new tab opened.** Reports proxy fragment (`#mcpProxyToken=...&mcpProxyPort=...`) via `ctx.report_progress`. User appends fragment to URL of their existing Colab tab and reloads. |
| `open_colab_browser_connection(notebook_url="https://colab.research.google.com/drive/<ID>")` | Opens that specific notebook with fragment appended. |
| Already connected (any form) | Returns `True`, no-op. |

The 60-second wait in `ColabProxyMiddleware.on_call_tool` is unchanged.

### Active install

`C:\Users\14294\.claude.json` `mcpServers["colab-mcp"]` was changed from:

```json
"args": ["git+https://github.com/googlecolab/colab-mcp"]
```

to (local path):

```json
"args": [
  "--from",
  "D:\\BaiduSyncdisk\\2024 to future\\koi chen\\tools\\colab-mcp",
  "colab-mcp"
]
```

Equivalent GitHub-fork form (if local path becomes inconvenient):

```json
"args": ["--from", "git+https://github.com/QiPan-Ronnie/colab-mcp", "colab-mcp"]
```

A Claude Code restart is required after changing the config for the new MCP server to load.

### Future

- Submit upstream PR to `googlecolab/colab-mcp` once we've used the fork in production for
  a few weeks and have collected feedback.
- Patch applies only to upstream commit `b9ab389`; when upstream advances, rebase the fork.
- The fix is small (2 files / +131 −7 lines) and backward-compatible (only adds an optional
  kwarg; preserves bool return type).
