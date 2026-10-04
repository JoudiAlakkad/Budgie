"""The growth check behind the run-time tests (`tests/unit/timing.py`) catches quadratic
and exponential code and passes linear code."""

import re
from math import log

import pytest

from tests.unit.timing import FLOOR_S, REPEATS, assert_linear, growth_limit


class FakeClock:
    """A clock that a fake workload advances, so the logic is tested without timing noise."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def workload(clock: FakeClock, seconds_for_size, calls: list[int] | None = None):
    def run(size: int) -> None:
        if calls is not None:
            calls.append(size)
        clock.now += seconds_for_size(size)

    return run


def test_growth_limit_sits_between_linear_and_quadratic() -> None:
    assert growth_limit((1_000, 4_000)) == 8
    assert 4 < growth_limit((1_000, 4_000)) < 16


def test_a_linear_function_passes() -> None:
    clock = FakeClock()
    growth = assert_linear(workload(clock, lambda n: n * 1e-6), int, (10_000, 40_000), clock=clock)
    assert growth.small_s == pytest.approx(0.01)
    assert growth.large_s == pytest.approx(0.04)
    assert growth.ratio == pytest.approx(4)


def test_a_quadratic_function_fails() -> None:
    clock = FakeClock()
    with pytest.raises(AssertionError, match=r"time grew 16\.0x for 4x the input"):
        assert_linear(workload(clock, lambda n: n * n * 1e-9), int, (10_000, 40_000), clock=clock)


def test_n_log_n_passes() -> None:
    # Sorting-like growth: 4 x log(40k)/log(10k) is about 4.6.
    clock = FakeClock()
    growth = assert_linear(
        workload(clock, lambda n: n * log(n) * 1e-7), int, (10_000, 40_000), clock=clock
    )
    assert growth.ratio == pytest.approx(4.6, abs=0.1)


def test_timings_below_the_floor_do_not_fail() -> None:
    # Quadratic, but 0.1 ms -> 1.6 ms: noise-sized, so the floor keeps the ratio low.
    clock = FakeClock()
    growth = assert_linear(
        workload(clock, lambda n: n * n * 1e-12), int, (10_000, 40_000), clock=clock
    )
    assert growth.large_s < FLOOR_S
    assert growth.ratio == pytest.approx(growth.large_s / FLOOR_S)


def test_the_fastest_of_the_repeats_counts() -> None:
    # The first call at each size is ten times slower (a cold cache, a busy runner): the
    # large size is re-measured because its first ratio (40x) fails, and the second
    # (4x) passes, so the third call is skipped.
    clock = FakeClock()
    seen: list[int] = []

    def noisy(n: int) -> float:
        seen.append(n)
        return n * 1e-6 * (10 if seen.count(n) == 1 else 1)

    growth = assert_linear(workload(clock, noisy), int, (10_000, 40_000), clock=clock)
    assert growth.small_s == pytest.approx(0.01)
    assert growth.ratio == pytest.approx(4)
    assert seen == [10_000] * 3 + [40_000] * 2


def test_a_failing_ratio_is_measured_every_repeat() -> None:
    clock = FakeClock()
    calls: list[int] = []
    with pytest.raises(AssertionError, match="time grew"):
        assert_linear(
            workload(clock, lambda n: n * n * 1e-9, calls), int, (10_000, 40_000), clock=clock
        )
    assert calls == [10_000] * REPEATS + [40_000] * REPEATS


def test_a_small_timing_at_the_floor_is_not_repeated() -> None:
    # 1 ms at the small size: a faster repeat would still count as the floor.
    clock = FakeClock()
    calls: list[int] = []
    growth = assert_linear(
        workload(clock, lambda n: n * 1e-7, calls), int, (10_000, 40_000), clock=clock
    )
    assert calls == [10_000, 40_000]
    assert growth.ratio == pytest.approx(0.004 / FLOOR_S)


def test_a_catastrophic_function_fails_at_the_small_size() -> None:
    # Exponential: the small size already passes the cap, and the large one is never run.
    clock = FakeClock()
    calls: list[int] = []
    with pytest.raises(AssertionError, match="cap"):
        assert_linear(
            workload(clock, lambda n: 2.0 ** (n / 1_000), calls), int, (10_000, 40_000), clock=clock
        )
    assert calls == [10_000]


def test_the_inputs_are_built_outside_the_timed_calls() -> None:
    clock = FakeClock()

    def make_input(size: int) -> int:
        clock.now += 100.0  # expensive to build, but not part of the measurement
        return size

    growth = assert_linear(
        workload(clock, lambda n: n * 1e-5), make_input, (1_000, 4_000), clock=clock
    )
    assert growth.ratio == pytest.approx(4)


# With the real clock. `[a ]*x` on a run of `a` retries the whole rest of the text at
# every position: about 12 ms at 3k characters and 190 ms at 12k on a dev machine.
QUADRATIC = re.compile(r"[a ]*x")
LINEAR = re.compile(r"a+x|a")


def test_a_quadratic_regex_fails_on_the_real_clock() -> None:
    with pytest.raises(AssertionError, match="time grew"):
        assert_linear(
            lambda text: list(QUADRATIC.finditer(text)), lambda n: "a" * n, (3_000, 12_000)
        )


def test_a_linear_regex_passes_on_the_real_clock() -> None:
    growth = assert_linear(lambda text: list(LINEAR.finditer(text)), lambda n: "a " * (n // 2))
    assert growth.ratio < growth_limit((1, 4))
