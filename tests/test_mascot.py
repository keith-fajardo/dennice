from dennice.tui.app import COMPACT_TERMINAL_MASCOT_GRID, MASCOT_PALETTE, TERMINAL_MASCOT_GRID, _quantize


def test_terminal_mascot_uses_a_fixed_solid_palette() -> None:
    assert _quantize((250, 60, 150)) == (241, 139, 174)
    assert _quantize((20, 20, 20)) in MASCOT_PALETTE


def test_terminal_mascot_is_a_native_24_by_20_grid() -> None:
    assert len(TERMINAL_MASCOT_GRID) == 20
    assert all(len(row) == 24 for row in TERMINAL_MASCOT_GRID)


def test_compact_home_mascot_is_a_native_12_by_10_grid() -> None:
    assert len(COMPACT_TERMINAL_MASCOT_GRID) == 10
    assert all(len(row) == 12 for row in COMPACT_TERMINAL_MASCOT_GRID)
