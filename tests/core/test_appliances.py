"""Appliance runs are found in plug readings and learned when plausible."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.solar_energy_insights.core.appliances import (
    ApplianceProfile,
    Run,
    RunTracker,
)

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)


def play(tracker: RunTracker, readings: list[tuple[int, float]]) -> list[Run]:
    """Feed (minute, watts) readings; the meter counts the energy exactly."""
    runs, meter, last = [], 100.0, None
    for minute, watts in readings:
        if last is not None:
            meter += last[1] * (minute - last[0]) / 60 / 1000
        last = (minute, watts)
        run = tracker.feed(T0 + timedelta(minutes=minute), watts, meter)
        if run:
            runs.append(run)
    return runs


def program(
    minutes: int, watts: float = 1200.0, pause_at: int | None = None
) -> list[tuple[int, float]]:
    readings = [
        (m, 0.0 if pause_at is not None and pause_at <= m < pause_at + 10 else watts)
        for m in range(minutes)
    ]
    return [*readings, *((minutes + m, 1.0) for m in range(30))]


def test_a_program_becomes_one_run() -> None:
    runs = play(RunTracker(), program(120))

    assert len(runs) == 1
    assert 1.9 < runs[0].hours < 2.1
    assert runs[0].kwh > 2


def test_a_short_pause_does_not_split_a_run() -> None:
    runs = play(RunTracker(), program(120, pause_at=60))

    assert len(runs) == 1


def test_a_blip_is_not_a_run() -> None:
    runs = play(
        RunTracker(),
        [(0, 0.0), (1, 800.0), (2, 0.0), *((m, 0.0) for m in range(3, 40))],
    )

    assert runs == []


def test_learns_from_plausible_runs_only() -> None:
    profile = ApplianceProfile(default_kwh=1.0, default_hours=2.5, max_kwh=4.0)
    assert not profile.learned
    assert profile.kwh == 1.0

    assert not profile.add(Run(T0, T0 + timedelta(hours=2), 40.0))  # a counter jump
    assert not profile.add(Run(T0, T0 + timedelta(minutes=3), 0.5))  # too short
    assert profile.add(Run(T0, T0 + timedelta(hours=2), 1.2))
    assert profile.add(Run(T0, T0 + timedelta(hours=3), 1.6))

    assert profile.learned
    assert 1.2 < profile.kwh < 1.6
    assert 2 < profile.hours < 3


def test_a_run_in_progress_survives_a_restart() -> None:
    tracker = RunTracker()
    play(tracker, [(m, 1200.0) for m in range(30)])

    restored = RunTracker.from_dict(tracker.as_dict())

    assert restored.running
