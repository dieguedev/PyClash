import math
import random

import pytest

from pyclash.policy.deploy_path import PathSettings, inside, make_trace

SIZE = (1280, 720)
# Contorno parecido a una zona roja real (rombo irregular), centrado en pantalla.
HULL = [(505, 330), (640, 205), (830, 180), (960, 305), (740, 478), (620, 450)]


def trace(seed, hull=HULL, settings=None):
    return make_trace(hull, SIZE, random.Random(seed), settings)


def test_same_seed_gives_the_same_trace():
    assert trace(5) == trace(5)


def test_different_seeds_give_different_traces():
    traces = [trace(s) for s in range(6)]
    assert len({t.points for t in traces}) == 6


def test_three_or_four_laps_plus_an_extra_piece():
    laps = {trace(s).laps for s in range(40)}
    assert laps == {3, 4}
    for s in range(10):
        t = trace(s)
        assert len(t.lap_ends) == t.laps + 1 and t.lap_ends[-1] == len(t.points)
        assert list(t.lap_ends) == sorted(t.lap_ends)


def test_never_starts_and_ends_at_the_same_place():
    for s in range(30):
        t = trace(s)
        (x0, y0), (x1, y1) = t.points[0], t.points[-1]
        assert math.hypot(x1 - x0, y1 - y0) > 25, s  # acaba tras un trozo extra de vuelta


def test_points_stay_in_the_visible_terrain_and_out_of_the_red_zone():
    cfg = PathSettings()
    for s in range(20):
        for x, y in trace(s).points:
            assert cfg.side * SIZE[0] - 1 <= x <= (1 - cfg.side) * SIZE[0] + 1
            assert cfg.top * SIZE[1] - 1 <= y <= cfg.bottom * SIZE[1] + 1  # sobre la barra de cartas
            assert not inside([(float(a), float(b)) for a, b in HULL], x, y)


def test_a_hull_near_the_frame_edge_still_gives_points_inside_the_frame():
    near_bottom = [(560, 560), (700, 470), (900, 520), (860, 640), (640, 650)]  # el trazo se sale por abajo y se recorta
    cfg = PathSettings()
    t = trace(3, near_bottom)
    assert all(y <= cfg.bottom * SIZE[1] + 1 for _, y in t.points)
    assert not any(inside([(float(a), float(b)) for a, b in near_bottom], x, y) for x, y in t.points)


def _angle_radius(points, center):
    return [(math.atan2(y - center[1], x - center[0]), math.hypot(x - center[0], y - center[1])) for x, y in points]


def test_laps_are_not_the_same_curve():
    t = trace(2)
    cx = sum(x for x, _ in HULL) / len(HULL)
    cy = sum(y for _, y in HULL) / len(HULL)
    laps = []
    start = 0
    for end in t.lap_ends[:-1]:
        laps.append(_angle_radius(t.points[start:end], (cx, cy)))
        start = end
    first, second = laps[0], laps[1]
    # radio medio por sector angular: en dos vueltas distintas no coincide
    def sectors(lap):
        out = [[] for _ in range(12)]
        for angle, radius in lap:
            out[int((angle + math.pi) / (2 * math.pi) * 12) % 12].append(radius)
        return [sum(v) / len(v) if v else None for v in out]

    diffs = [abs(a - b) for a, b in zip(sectors(first), sectors(second)) if a is not None and b is not None]
    assert diffs and max(diffs) > 4  # píxeles


def test_the_path_is_not_a_perfect_ellipse_or_a_straight_line():
    t = trace(4)
    pts = t.points
    # no hay tramos largos perfectamente rectos: la dirección cambia a lo largo del trazo
    headings = [math.atan2(pts[i + 8][1] - pts[i][1], pts[i + 8][0] - pts[i][0]) for i in range(0, len(pts) - 8, 8)]
    turns = [abs(((b - a + math.pi) % (2 * math.pi)) - math.pi) for a, b in zip(headings, headings[1:])]
    assert max(turns) - min(turns) > 0.05  # el giro no es constante (una elipse perfecta lo mantendría suave y regular)


def test_waits_are_irregular_and_total_a_plausible_time():
    t = trace(1)
    assert len(t.waits) == len(t.points)
    assert len({round(w, 5) for w in t.waits}) > len(t.waits) // 2  # no hay un intervalo fijo
    assert max(t.waits) - min(t.waits) > 0.003
    assert 3 < sum(t.waits) < 30  # unos segundos de gesto


def test_direction_and_start_angle_vary_between_attacks():
    cx = sum(x for x, _ in HULL) / len(HULL)
    cy = sum(y for _, y in HULL) / len(HULL)
    starts = set()
    senses = set()
    for s in range(30):
        t = trace(s)
        a0 = math.atan2(t.points[0][1] - cy, t.points[0][0] - cx)
        a1 = math.atan2(t.points[10][1] - cy, t.points[10][0] - cx)
        starts.add(round(a0, 1))
        senses.add(((a1 - a0 + math.pi) % (2 * math.pi) - math.pi) > 0)
    assert len(starts) > 10 and senses == {True, False}


def test_degenerate_contour_is_rejected():
    with pytest.raises(ValueError):
        trace(1, [(10, 10), (20, 20)])
