"""Debounce logic for BTN1 (pure part of button.py)."""

import button


def test_idle_high_never_fires():
    deb = button.Debouncer(40)
    for t in range(0, 1000, 10):
        assert deb.update(1, t) is False


def test_press_fires_once_after_debounce():
    deb = button.Debouncer(40)
    assert deb.update(0, 0) is False
    assert deb.update(0, 39) is False
    assert deb.update(0, 40) is True
    assert deb.update(0, 100) is False
    assert deb.update(0, 200) is False


def test_contact_bounce_is_absorbed():
    deb = button.Debouncer(40)
    events = [
        deb.update(0, 0),
        deb.update(1, 5),
        deb.update(0, 10),
        deb.update(1, 15),
        deb.update(0, 20),
        deb.update(0, 60),
    ]
    assert events == [False, False, False, False, False, True]


def test_short_glitch_does_not_fire():
    deb = button.Debouncer(40)
    assert deb.update(0, 0) is False
    assert deb.update(1, 20) is False
    assert deb.update(1, 100) is False


def test_release_does_not_fire_and_next_press_does():
    deb = button.Debouncer(40)
    assert deb.update(0, 0) is False
    assert deb.update(0, 40) is True

    # release with bounce: no event
    assert deb.update(1, 50) is False
    assert deb.update(0, 55) is False
    assert deb.update(1, 60) is False
    assert deb.update(1, 100) is False

    # next press fires again
    assert deb.update(0, 110) is False
    assert deb.update(0, 150) is True


def test_coerces_truthy_levels():
    deb = button.Debouncer(40)
    assert deb.update(0, 0) is False
    assert deb.update(False, 40) is True
    assert deb.update(True, 80) is False


def test_initial_level_does_not_fire():
    deb = button.Debouncer(40)
    deb.set_level(0)
    for t in range(0, 1000, 10):
        assert deb.update(0, t) is False
