from agent.db145_ground_operator.observability import (
    PatchObservability,
    SceneMotion,
    select_heldout_groups,
    select_patch_pair,
    select_scene_roles,
)


def test_scene_roles_are_unique_and_wet_role_is_historical():
    motions = [
        SceneMotion("02a00399-3857-444e-8db3-a8f58489c394", 12, 20, 0.99, 0.02, 0.1),
        SceneMotion("02678d04-cc9f-3148-9f95-1ba66347dff9", 15, 30, 0.92, 0.20, 0.5),
        SceneMotion("2c652f9e-8db8-3572-aa49-fae1344a875b", 16, 35, 0.60, 1.50, 5.0),
        SceneMotion("8749f79f-a30b-3c3f-8a44-dbfa682bbef1", 2, 3, 0.20, 2.0, 3.0),
    ]
    roles = select_scene_roles(reversed(motions))
    assert roles["dry_straight"].startswith("02a00399")
    assert roles["dry_turn"].startswith("2c652f9e")
    assert roles["wet_or_specular"].startswith("05fa5048")
    assert len(set(roles.values())) == 3


def _patch(identifier, x, score_parts, *, evidence=True):
    coverage, views, angular, phase, cameras, aspect = score_parts
    return PatchObservability(
        identifier,
        (x, 0.0),
        coverage,
        views,
        angular,
        phase,
        cameras,
        aspect,
        0.02,
        evidence,
    )


def test_patch_selection_is_deterministic_high_low_and_separated():
    candidates = [
        _patch("low_near", 1.0, (0.21, 3, 0.1, 0.1, 0.2, 30)),
        _patch("high", 0.0, (0.95, 60, 0.9, 0.9, 1.0, 3)),
        _patch("low_far", 5.0, (0.25, 4, 0.1, 0.1, 0.2, 30)),
        _patch("invalid", 10.0, (0.01, 1, 0.0, 0.0, 0.1, 40), evidence=False),
    ]
    high, low = select_patch_pair(reversed(candidates))
    assert high.patch_id == "high"
    assert low.patch_id == "low_far"


def test_heldout_camera_groups_are_disjoint_and_large_enough():
    counts = {f"{camera}:{time}": 10 for camera in ("a", "b", "c", "d", "e") for time in range(4)}
    cameras = {group: group.split(":")[0] for group in counts}
    times = {group: int(group.split(":")[1]) for group in counts}
    split = select_heldout_groups(counts, group_camera=cameras, group_time=times)
    assert split.strategy == "complete_camera"
    assert set(split.training_groups).isdisjoint(split.heldout_groups)
    assert split.heldout_fraction >= 0.10
