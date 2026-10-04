"""Growth checks for the run-time tests: assert that a function scales linearly.

A fixed wall-clock budget measures the machine as much as the code: a linear rule that
takes 0.26 s locally took 0.6 s on a shared CI runner and broke a 0.5 s budget. What these
tests guard is the *shape* of the cost, so `assert_linear` times the function at a size
and at four times that size, and fails if the time grew much faster than the input.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Default sizes, in characters. At 50k characters a quadratic regex takes 1.5-3 s on a
# dev machine (`a*b` or `[a ]*x` on a run of `a`), and at 12.5k still 90-200 ms, far above
# FLOOR_S, so the ratio is measured on real time and not on the floor.
SMALL = 12_500
SIZES = (SMALL, 4 * SMALL)

# Each size is timed up to REPEATS times and the fastest run counts: noise (another
# process, a GC pause, a cold cache) only ever adds time, so the minimum is the best
# estimate.
REPEATS = 3

# Below FLOOR_S a timing is mostly timer and interpreter noise: 0.1 ms -> 0.6 ms would read
# as a "6x" growth. The small timing is raised to the floor before dividing, so tiny
# timings can't fail the check. 5 ms is about ten times the noise of a `perf_counter`
# measurement on a busy runner, and a quadratic rule at SMALL takes far longer.
FLOOR_S = 0.005

# Absolute backstop per call. Linear rules take tens of milliseconds at the large size,
# so even a runner ten times slower stays far below it. It catches a catastrophic
# (exponential) blow-up, and because the small size runs first, such a function fails
# there and the large size, where it could run for hours, is never tried.
CAP_S = 5.0


def growth_limit(sizes: tuple[int, int]) -> float:
    """The largest accepted `t(large) / t(small)`: `scale ** 1.5`, so 8 for sizes (n, 4n).

    Linear code grows by `scale` (4) and quadratic code by `scale ** 2` (16). The limit
    sits at their geometric mean, which leaves a factor of 2 for noise on either side:
    a linear function must look twice as slow as it is to fail, and a quadratic one twice
    as fast as it is to pass.
    """
    small, large = sizes
    return (large / small) ** 1.5


@dataclass(frozen=True)
class Growth:
    """The fastest timings at the two sizes, and their ratio (small raised to the floor)."""

    small_s: float
    large_s: float
    ratio: float


def time_once(
    func: Callable[[Any], object],
    arg: Any,
    *,
    cap_s: float = CAP_S,
    clock: Callable[[], float] = time.perf_counter,
) -> float:
    """The duration of one call of `func(arg)`; fails if it reaches `cap_s`."""
    started = clock()
    func(arg)
    elapsed = clock() - started
    assert elapsed < cap_s, f"one call took {elapsed:.3f} s, cap {cap_s} s"
    return elapsed


def assert_linear(
    func: Callable[[Any], object],
    make_input: Callable[[int], Any],
    sizes: tuple[int, int] = SIZES,
    *,
    floor_s: float = FLOOR_S,
    cap_s: float = CAP_S,
    repeats: int = REPEATS,
    clock: Callable[[], float] = time.perf_counter,
) -> Growth:
    """Assert that `func(make_input(size))` grows about linearly from the small size to the
    large one: best of `repeats` at each size, `t(large) / max(t(small), floor_s)` below
    `growth_limit(sizes)`. Inputs are built outside the timed calls, the small one first.

    Repeats that can't change the verdict are skipped, so the result is the same as with
    all of them: once a small timing is at or below the floor, a faster one would still
    count as the floor; and once the ratio is below the limit, a faster large timing would
    only lower it. Only a failing ratio is re-measured, up to `repeats` times.
    """
    small, large = sizes
    assert 0 < small < large and repeats >= 1
    limit = growth_limit(sizes)

    arg = make_input(small)
    small_s = float("inf")
    for _ in range(repeats):
        small_s = min(small_s, time_once(func, arg, cap_s=cap_s, clock=clock))
        if small_s <= floor_s:
            break

    arg = make_input(large)
    large_s = float("inf")
    for _ in range(repeats):
        large_s = min(large_s, time_once(func, arg, cap_s=cap_s, clock=clock))
        if large_s / max(small_s, floor_s) < limit:
            break

    ratio = large_s / max(small_s, floor_s)
    assert ratio < limit, (
        f"time grew {ratio:.1f}x for {large / small:g}x the input "
        f"({small_s * 1000:.1f} ms -> {large_s * 1000:.1f} ms), limit {limit:g}x"
    )
    return Growth(small_s, large_s, ratio)
