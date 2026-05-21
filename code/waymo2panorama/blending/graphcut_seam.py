"""
Graph-cut optimal seam selection for ERP multi-camera stitching (route 11 / 新-B).

Replaces the cos^2 angular-fall-off weights produced by `render_camera_to_erp`
with hard 0/1 (slightly feathered) per-camera masks placed along *energy-
minimizing seams* in pairwise overlap regions.

Why:
  - Cos^2 weights collapse onto an analytic midline in each pairwise overlap.
    After 5-band Laplacian blending, the highest frequency band still leaves
    a vertical seam at that midline — visible cutting through buildings,
    vehicles and tree crowns.
  - Energy-minimizing seams snap to flat / homogeneous regions (sky, road),
    making the seam disappear into texture even before blending.

Method (per adjacent ERP cam pair (a, b)):
  1. Compute a per-pixel energy E = alpha*color_diff + beta*grad_diff
     + gamma*boundary_penalty on the overlap bbox only.
  2. Build a 4-connected min-cut graph with source = "only A" region and
     sink  = "only B" region. Edge weight ~ E(p) + E(q).
  3. Run min-cut -> binary assignment.
  4. Convert per-pair assignments to per-cam {0,1} masks; light Gaussian
     feather; return as drop-in replacement for cos^2 weights into
     `multiband_blend`. No blender patch needed.

The module reads `cam_axes_erp` (per-cam ERP-column position of the optical
axis) only to determine pair adjacency by sorting on theta with wrap.

Dependencies: numpy, opencv (cv2), maxflow (PyMaxflow). PyMaxflow has
Windows wheels for py3.9-3.13 on PyPI as of 2026-05.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

try:
    import maxflow  # type: ignore
    _HAVE_MAXFLOW = True
except ImportError:
    _HAVE_MAXFLOW = False


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------


def compute_pair_overlap_energy(
    slab_a: np.ndarray,
    slab_b: np.ndarray,
    alpha_a: np.ndarray,
    alpha_b: np.ndarray,
    alpha_w: float = 1.0,
    beta_w: float = 0.5,
    gamma_w: float = 0.1,
) -> tuple[np.ndarray, np.ndarray]:
    """Pairwise overlap energy field for graph-cut seam selection.

    Args:
        slab_a, slab_b: (H, W, 3) float32 RGB in [0, 255]
        alpha_a, alpha_b: (H, W) bool (or 0/1) coverage masks
        alpha_w, beta_w, gamma_w: weights for color / gradient / boundary terms

    Returns:
        energy: (H, W) float32, only meaningful inside overlap; ~[0, 1] scale
        overlap: (H, W) bool, True where BOTH cams cover the pixel
    """
    a = alpha_a.astype(bool)
    b = alpha_b.astype(bool)
    overlap = a & b

    H, W = slab_a.shape[:2]
    if not overlap.any():
        return np.zeros((H, W), dtype=np.float32), overlap

    # --- color term: per-pixel |slab_a - slab_b|, mean over 3 channels, /255 ---
    diff = np.abs(slab_a.astype(np.float32) - slab_b.astype(np.float32))
    color_diff = diff.mean(axis=2) / 255.0  # in [0, 1]

    # --- gradient term: Sobel magnitude difference (grayscale), normalized ---
    a_gray = (
        0.299 * slab_a[..., 0] + 0.587 * slab_a[..., 1] + 0.114 * slab_a[..., 2]
    ).astype(np.float32)
    b_gray = (
        0.299 * slab_b[..., 0] + 0.587 * slab_b[..., 1] + 0.114 * slab_b[..., 2]
    ).astype(np.float32)
    gx_a = cv2.Sobel(a_gray, cv2.CV_32F, 1, 0, ksize=3)
    gy_a = cv2.Sobel(a_gray, cv2.CV_32F, 0, 1, ksize=3)
    gx_b = cv2.Sobel(b_gray, cv2.CV_32F, 1, 0, ksize=3)
    gy_b = cv2.Sobel(b_gray, cv2.CV_32F, 0, 1, ksize=3)
    mag_a = np.sqrt(gx_a * gx_a + gy_a * gy_a)
    mag_b = np.sqrt(gx_b * gx_b + gy_b * gy_b)
    grad_diff = np.abs(mag_a - mag_b)
    # Normalize gradient diff by an empirical scale (max within overlap)
    g_max = float(grad_diff[overlap].max()) if overlap.any() else 1.0
    if g_max < 1e-6:
        g_max = 1.0
    grad_diff = grad_diff / g_max  # in [0, 1]

    # --- boundary term: distance to overlap horizontal midline (in cols) ---
    # Encourages cut to stay close to the geometric midline (prevents pathological
    # wrap-around through identical-sky regions). Cheap stand-in: for each row,
    # find the half-width and the midpoint of the overlap span, then write
    # |u - midline_u| / half_width into the boundary term.
    boundary = np.zeros((H, W), dtype=np.float32)
    # Use vectorized per-row: argmax(overlap) and argmax(overlap[::-1]) give first/last True
    for v in range(H):
        row = overlap[v]
        if not row.any():
            continue
        cols = np.flatnonzero(row)
        u_min = int(cols[0])
        u_max = int(cols[-1])
        # detect wrap-around (overlap split across left/right edges of ERP)
        if u_max - u_min > W * 0.6 and (cols[1:] - cols[:-1] > 1).any():
            # gap exists - the overlap probably wraps; skip boundary term
            continue
        mid = 0.5 * (u_min + u_max)
        half = max(1.0, 0.5 * (u_max - u_min))
        boundary[v, u_min:u_max + 1] = np.abs(np.arange(u_min, u_max + 1) - mid) / half

    energy = alpha_w * color_diff + beta_w * grad_diff + gamma_w * boundary
    # Mask outside overlap to 0 (so cut graph won't pay attention there)
    energy = np.where(overlap, energy, 0.0).astype(np.float32)
    return energy, overlap


# ---------------------------------------------------------------------------
# Seeds (source/sink) for the binary cut
# ---------------------------------------------------------------------------


def build_pair_seeds(
    alpha_a: np.ndarray, alpha_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Source = pixels only A covers (must be label-A), sink = pixels only B covers.

    Returns (source_seed, sink_seed) bool (H, W).
    """
    a = alpha_a.astype(bool)
    b = alpha_b.astype(bool)
    source_seed = a & (~b)  # must go to label A (cam-a)
    sink_seed = b & (~a)    # must go to label B (cam-b)
    return source_seed, sink_seed


# ---------------------------------------------------------------------------
# Min-cut (PyMaxflow on the overlap bbox)
# ---------------------------------------------------------------------------


@dataclass
class _BBox:
    v0: int
    v1: int  # exclusive
    u0: int
    u1: int  # exclusive

    @property
    def h(self) -> int:
        return self.v1 - self.v0

    @property
    def w(self) -> int:
        return self.u1 - self.u0


def _overlap_bbox(overlap: np.ndarray, pad: int = 1) -> _BBox | None:
    if not overlap.any():
        return None
    H, W = overlap.shape
    ys, xs = np.nonzero(overlap)
    v0 = max(0, int(ys.min()) - pad)
    v1 = min(H, int(ys.max()) + 1 + pad)
    u0 = max(0, int(xs.min()) - pad)
    u1 = min(W, int(xs.max()) + 1 + pad)
    return _BBox(v0, v1, u0, u1)


def find_optimal_seam(
    energy: np.ndarray,
    overlap: np.ndarray,
    source_seed: np.ndarray,
    sink_seed: np.ndarray,
    inf_cap: float = 1e6,
    edge_eps: float = 1e-3,
) -> np.ndarray:
    """Min-cut on overlap bbox -> int8 (H, W) assignment in {0=A, 1=B, -1=outside}.

    Source-region (only A) is constrained to label 0.
    Sink-region   (only B) is constrained to label 1.
    Overlap pixels chosen by min-cut to minimize sum of edge energies.
    """
    H, W = energy.shape
    assign = np.full((H, W), -1, dtype=np.int8)

    bbox = _overlap_bbox(overlap | source_seed | sink_seed, pad=1)
    if bbox is None:
        return assign

    e_b = energy[bbox.v0:bbox.v1, bbox.u0:bbox.u1].astype(np.float32)
    o_b = overlap[bbox.v0:bbox.v1, bbox.u0:bbox.u1]
    s_b = source_seed[bbox.v0:bbox.v1, bbox.u0:bbox.u1]
    t_b = sink_seed[bbox.v0:bbox.v1, bbox.u0:bbox.u1]
    h, w = e_b.shape

    if _HAVE_MAXFLOW:
        g = maxflow.GraphFloat()
        node_ids = g.add_grid_nodes((h, w))

        # 4-conn neighbor edges with weight = E(p) + E(q) + epsilon
        # (epsilon keeps the graph well-defined where energy is near zero.)
        # Horizontal edges
        e_h = e_b[:, :-1] + e_b[:, 1:] + edge_eps
        # Vertical edges
        e_v = e_b[:-1, :] + e_b[1:, :] + edge_eps

        # PyMaxflow add_edge takes pairs of node IDs; use vectorized helpers
        # via add_grid_edges with explicit structure.
        # We use two simple loops over offsets for clarity (h,w small ~200x400).
        # Horizontal:
        n_h = node_ids[:, :-1].ravel()
        n_h2 = node_ids[:, 1:].ravel()
        w_h = e_h.ravel()
        for i in range(n_h.size):
            g.add_edge(int(n_h[i]), int(n_h2[i]), float(w_h[i]), float(w_h[i]))
        # Vertical:
        n_v = node_ids[:-1, :].ravel()
        n_v2 = node_ids[1:, :].ravel()
        w_v = e_v.ravel()
        for i in range(n_v.size):
            g.add_edge(int(n_v[i]), int(n_v2[i]), float(w_v[i]), float(w_v[i]))

        # T-edges: source-seed -> inf to source, sink-seed -> inf to sink
        # add_tedge(node, cap_source, cap_sink); pixel will keep "source" label
        # (label 0) if cap_source > cap_sink after maxflow.
        # For overlap pixels with no seed: give a tiny symmetric bias so the
        # solver is well-conditioned (use 0 for both — equal preference).
        for vi in range(h):
            for ui in range(w):
                nid = int(node_ids[vi, ui])
                if s_b[vi, ui]:
                    g.add_tedge(nid, inf_cap, 0.0)
                elif t_b[vi, ui]:
                    g.add_tedge(nid, 0.0, inf_cap)
                elif not o_b[vi, ui]:
                    # Outside any coverage: tie-break to A (won't matter, weight=0)
                    g.add_tedge(nid, edge_eps, 0.0)
                # else: pure-overlap pixel, leave neutral

        g.maxflow()
        seg = g.get_grid_segments(node_ids)  # bool: True -> sink-side (label 1 = B)
        label = seg.astype(np.int8)
    else:
        label = _find_optimal_seam_scipy(e_b, o_b, s_b, t_b, edge_eps=edge_eps)

    # Write back into full ERP-sized assignment array
    full = np.full((h, w), -1, dtype=np.int8)
    any_cov = o_b | s_b | t_b
    full[any_cov] = label[any_cov]
    assign[bbox.v0:bbox.v1, bbox.u0:bbox.u1] = full
    return assign


def _find_optimal_seam_scipy(
    e_b: np.ndarray,
    o_b: np.ndarray,
    s_b: np.ndarray,
    t_b: np.ndarray,
    edge_eps: float = 1e-3,
    inf_cap: float = 1e6,
) -> np.ndarray:
    """Fallback min-cut using scipy.sparse.csgraph.maximum_flow.

    Slower than PyMaxflow by 3-5x. Same semantics: True -> sink-side (B).
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import maximum_flow

    h, w = e_b.shape
    N = h * w
    # Node indexing: 0..N-1 are pixels, N is source, N+1 is sink.
    src = N
    snk = N + 1

    rows: list[int] = []
    cols: list[int] = []
    caps: list[int] = []

    def add_edge(u: int, v: int, c: float) -> None:
        # scipy maximum_flow requires int capacities; scale by 1000.
        ci = max(1, int(c * 1000.0))
        rows.append(u); cols.append(v); caps.append(ci)
        rows.append(v); cols.append(u); caps.append(ci)

    # Pixel grid edges
    for vi in range(h):
        for ui in range(w):
            p = vi * w + ui
            if ui + 1 < w:
                q = vi * w + (ui + 1)
                add_edge(p, q, e_b[vi, ui] + e_b[vi, ui + 1] + edge_eps)
            if vi + 1 < h:
                q = (vi + 1) * w + ui
                add_edge(p, q, e_b[vi, ui] + e_b[vi + 1, ui] + edge_eps)

    # T-edges (directed -- src->p infinite for source seeds, p->snk inf for sink)
    INF = int(inf_cap)
    for vi in range(h):
        for ui in range(w):
            p = vi * w + ui
            if s_b[vi, ui]:
                rows.append(src); cols.append(p); caps.append(INF)
                rows.append(p); cols.append(src); caps.append(0)
            elif t_b[vi, ui]:
                rows.append(p); cols.append(snk); caps.append(INF)
                rows.append(snk); cols.append(p); caps.append(0)

    graph = csr_matrix(
        (np.array(caps, dtype=np.int32), (np.array(rows), np.array(cols))),
        shape=(N + 2, N + 2),
    )
    res = maximum_flow(graph, src, snk, method="dinic")
    flow = res.flow
    # Residual graph: nodes reachable from src along positive-residual edges -> source-side
    # Residual capacity for edge (u,v) = cap(u,v) - flow(u,v).
    # We'll do a BFS from src.
    from collections import deque
    rcap = graph - flow
    rcap = rcap.tocsr()
    visited = np.zeros(N + 2, dtype=bool)
    dq = deque([src])
    visited[src] = True
    while dq:
        u = dq.popleft()
        start = rcap.indptr[u]; end = rcap.indptr[u + 1]
        for idx in range(start, end):
            v = int(rcap.indices[idx])
            if rcap.data[idx] > 0 and not visited[v]:
                visited[v] = True
                dq.append(v)
    # Source-side: visited[p] == True -> label A (False on label-bool); else label B (True)
    src_side = visited[:N].reshape(h, w)
    label = (~src_side).astype(np.int8)  # True (sink-side) -> 1 (B)
    return label


# ---------------------------------------------------------------------------
# Per-pair adjacency from ERP-column positions of optical axes
# ---------------------------------------------------------------------------


def _erp_column_for_axis_world(T_ego_cam: np.ndarray, W_erp: int) -> float:
    """Return the ERP column (in [0, W)) corresponding to the camera optical axis."""
    R = T_ego_cam[:3, :3]
    axis_ego = R @ np.array([0.0, 0.0, 1.0])  # +z in cam = optical axis
    x, y = float(axis_ego[0]), float(axis_ego[1])
    theta = np.arctan2(y, x)  # ego: x forward, y left. theta in (-pi, pi]
    # ERP convention (see sphere_projection.py): theta = pi - (u+0.5)/W * 2pi
    # so u = ((pi - theta) / (2 pi)) * W - 0.5
    u = ((np.pi - theta) / (2.0 * np.pi)) * W_erp - 0.5
    u = u % W_erp
    return float(u)


def _pair_overlap_cols(alpha_a: np.ndarray, alpha_b: np.ndarray) -> int:
    return int((alpha_a.astype(bool) & alpha_b.astype(bool)).sum())


def build_cam_pair_adjacency(
    cam_axes_erp: list[float],
    alphas: list[np.ndarray],
    min_overlap_px: int = 1024,
) -> list[tuple[int, int]]:
    """Return pairs (i, j) of cam indices that are adjacent in ERP and share overlap.

    Adjacency = nearest neighbor in cam_axes_erp (circular sort), filtered by
    actual overlap pixel count >= min_overlap_px.
    """
    n = len(cam_axes_erp)
    order = sorted(range(n), key=lambda i: cam_axes_erp[i])
    pairs: list[tuple[int, int]] = []
    for k in range(n):
        i = order[k]
        j = order[(k + 1) % n]
        if _pair_overlap_cols(alphas[i], alphas[j]) >= min_overlap_px:
            pairs.append((i, j))
    return pairs


# ---------------------------------------------------------------------------
# Top-level orchestrator (drop-in for cos^2 weights)
# ---------------------------------------------------------------------------


def apply_graphcut_seams(
    slabs: list[np.ndarray],
    alphas: list[np.ndarray],
    cos2_weights: list[np.ndarray],
    cam_axes_erp: list[float],
    feather_sigma: float = 3.0,
    energy_weights: tuple[float, float, float] = (1.0, 0.5, 0.1),
    min_overlap_px: int = 1024,
    seam_log: list[dict] | None = None,
) -> list[np.ndarray]:
    """Replace cos^2 weights with hard-cut + light-feather masks from min-cut seams.

    Procedure:
      1. Initialize per-cam new weight = cos^2 weight, restricted to that cam's alpha.
      2. For each adjacent pair (i, j) sharing >= min_overlap_px overlap:
         - Compute pair energy.
         - Run min-cut. Assignment maps overlap pixels to either i or j.
         - In the pair's overlap region, set new_w[i] = 1 where assigned to i (else 0),
           and symmetrically for j. Pixels outside the overlap keep their prior values.
      3. Apply a light Gaussian blur to each cam's new weight to seed the multiband
         lowest band with a smooth transition (multiband still does the band-wise
         splitting; this just avoids the brittle 0/1 boundary at level 0).

    Triple-overlap corners (3+ cams seeing the same pixel) are not optimal-multi-label;
    they are resolved by the standard cos^2 argmax fallback (i.e., pixels not
    touched by any pair update keep the cos^2 weight ratio).

    Args:
        slabs: list of (H, W, 3) float32 RGB
        alphas: list of (H, W) bool/0-1 coverage masks
        cos2_weights: list of (H, W) float32 (current cos^2 fallback weights)
        cam_axes_erp: list of float, the ERP column of each cam optical axis
        feather_sigma: Gaussian sigma in pixels for post-cut weight blur
        energy_weights: (alpha, beta, gamma) -> color, gradient, boundary
        min_overlap_px: pairs with fewer overlap pixels skipped (cos^2 falls through)
        seam_log: optional list to append per-pair info dicts into

    Returns:
        new_weights: list of (H, W) float32 in [0, 1]; will be re-normalized by
        `multiband_blend` per pixel.
    """
    n_cams = len(slabs)
    H, W = slabs[0].shape[:2]
    a_bool = [a.astype(bool) for a in alphas]

    # Start from cos^2 weights (so anything graph-cut doesn't touch falls back gracefully).
    new_w = [w.copy().astype(np.float32) for w in cos2_weights]
    # Hard-zero outside alpha
    for i in range(n_cams):
        new_w[i] = np.where(a_bool[i], new_w[i], 0.0)

    pairs = build_cam_pair_adjacency(cam_axes_erp, a_bool, min_overlap_px=min_overlap_px)

    # Track per-cam pair-claimed mask so each cam's weight in pair regions is
    # exactly the cut result (we don't want cos^2 leftover bleeding in).
    pair_claim = [np.zeros((H, W), dtype=bool) for _ in range(n_cams)]
    pair_hard = [np.zeros((H, W), dtype=np.float32) for _ in range(n_cams)]
    a_w, b_w, c_w = energy_weights

    for (i, j) in pairs:
        energy, overlap = compute_pair_overlap_energy(
            slabs[i], slabs[j], a_bool[i], a_bool[j],
            alpha_w=a_w, beta_w=b_w, gamma_w=c_w,
        )
        if not overlap.any():
            continue
        s_seed, t_seed = build_pair_seeds(a_bool[i], a_bool[j])
        assign = find_optimal_seam(energy, overlap, s_seed, t_seed)

        # Within pair coverage (overlap U s_seed U t_seed), record hard assignment.
        # Outside pair coverage, leave alone.
        pair_cov = overlap | s_seed | t_seed
        # Pixels assigned to i (label 0) -> claim by i
        to_i = pair_cov & (assign == 0)
        to_j = pair_cov & (assign == 1)
        # Force any source-seed/sink-seed assignment (defensive)
        to_i |= s_seed
        to_j |= t_seed

        pair_claim[i] |= pair_cov
        pair_claim[j] |= pair_cov
        # Set hard mask: 1.0 where this cam wins, 0.0 where opponent wins
        pair_hard[i] = np.where(to_i, 1.0, np.where(to_j, 0.0, pair_hard[i])).astype(np.float32)
        pair_hard[j] = np.where(to_j, 1.0, np.where(to_i, 0.0, pair_hard[j])).astype(np.float32)

        if seam_log is not None:
            seam_log.append({
                "pair": (i, j),
                "overlap_px": int(overlap.sum()),
                "assigned_i_px": int(to_i.sum()),
                "assigned_j_px": int(to_j.sum()),
                "energy_mean_on_overlap": float(energy[overlap].mean()),
            })

    # Merge into new_w: where claimed by any pair, use hard mask; else cos^2.
    for i in range(n_cams):
        new_w[i] = np.where(pair_claim[i], pair_hard[i], new_w[i]).astype(np.float32)
        new_w[i] = np.where(a_bool[i], new_w[i], 0.0)

    # Light feather to give multiband lowest band a smooth seed
    if feather_sigma > 0:
        k = max(3, int(feather_sigma * 4) | 1)  # odd kernel
        for i in range(n_cams):
            new_w[i] = cv2.GaussianBlur(new_w[i], (k, k), feather_sigma)
            # Keep zero outside alpha (blur can leak in by one half-kernel)
            new_w[i] = np.where(a_bool[i], new_w[i], 0.0).astype(np.float32)
    return new_w


# ---------------------------------------------------------------------------
# Visualization helpers (used by the driver, kept here for cohesion)
# ---------------------------------------------------------------------------


def draw_seam_overlay_on_erp(
    erp_rgb: np.ndarray,
    weights: list[np.ndarray],
    color: tuple[int, int, int] = (255, 0, 0),
    thickness: int = 2,
) -> np.ndarray:
    """Draw the dominant-cam boundary lines on an ERP image, in `color` BGR or RGB."""
    H, W = erp_rgb.shape[:2]
    if not weights:
        return erp_rgb.copy()
    stack = np.stack(weights, axis=0)
    argmax_id = stack.argmax(axis=0).astype(np.int32)
    coverage = stack.max(axis=0) > 1e-6
    argmax_id = np.where(coverage, argmax_id, -1)

    edge = np.zeros((H, W), dtype=bool)
    # Horizontal & vertical diffs in argmax id mark seams
    edge[:, 1:] |= (argmax_id[:, 1:] != argmax_id[:, :-1])
    edge[1:, :] |= (argmax_id[1:, :] != argmax_id[:-1, :])
    # Exclude transitions to/from -1 (coverage boundary), focus on cam-cam seams
    bdy_h = (argmax_id[:, 1:] == -1) | (argmax_id[:, :-1] == -1)
    bdy_v = (argmax_id[1:, :] == -1) | (argmax_id[:-1, :] == -1)
    edge[:, 1:] &= ~bdy_h
    edge[1:, :] &= ~bdy_v

    if thickness > 1:
        k = thickness
        kernel = np.ones((k, k), dtype=np.uint8)
        edge = cv2.dilate(edge.astype(np.uint8), kernel).astype(bool)

    out = erp_rgb.copy()
    out[edge] = np.array(color, dtype=out.dtype)
    return out
