# Tests

```
pip install pytest PySide6 --break-system-packages   # if not already present
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
```

89 tests, ~4s.

## What's here

- `test_command_builders.py`, `test_progress.py` - unit tests against the
  `PURE CORE` sections of `hdr_to_sdr_gui_qt.py`. No Qt import, no
  `QApplication`. These are what the whole migration was for: before it,
  there was no way to test a command string without building the entire
  UI first.

- `test_golden_snapshot.py`, `test_theme_smoke.py` - regression tests that
  *do* need Qt (they construct a real `MainWindow`). They replay
  `tools/golden_capture.py` / `tools/theme_smoke.py` and diff the result
  against `golden/*.json`, committed in this repo as the pinned "before"
  state from Step 0 of the migration.

## When a golden test fails

Two different reasons, and they need different responses:

1. **You changed behaviour on purpose** (a real encoder tweak, a new
   option, a genuine bug fix). Regenerate the snapshot, but read the diff
   before committing it - don't let a failing test train you to blindly
   re-run the generator:

   ```
   QT_QPA_PLATFORM=offscreen python3 tools/golden_capture.py golden/commands.json
   git diff golden/commands.json
   ```

2. **You were refactoring and didn't mean to change anything.** The diff
   *is* the bug. Fix the code, not the snapshot.

`golden/commands.pre-testclip-fix.json` is kept as a record of the one
intentional behaviour change made during this migration: the HandBrake
test-clip command used to be built by hand and had silently drifted from
the real conversion command (missing denoise/deband/sharpen/crop-mode/
bitrate-mode). It's now derived from `build_handbrake_command()` directly,
so that can't happen again - see
`TestBuildTestClipCommand.test_handbrake_clip_drops_two_pass_but_keeps_filters`
in `test_command_builders.py`.

## Adding a new setting

If you add a widget that a command builder needs to read:

1. Add a field to `ConversionSettings`.
2. Read it in `MainWindow.collect_settings()`.
3. Use it in the relevant `build_*` function - never read the widget
   directly from inside a `build_*` function.
4. Add a test in `test_command_builders.py` before wiring up the UI side,
   if you can - the pure functions don't need a window to test.
