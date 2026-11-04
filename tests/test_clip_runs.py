from hdr2sdr.runtime.clip_runs import ClipRunManager


def test_each_test_clip_run_gets_an_isolated_temp_directory(tmp_path):
    runs = ClipRunManager(tmp_path)
    first_generation, first_dir = runs.begin()
    second_generation, second_dir = runs.begin()

    assert first_dir != second_dir
    assert first_dir.is_dir() and second_dir.is_dir()
    assert not runs.is_current(first_generation)
    assert runs.is_current(second_generation)

    runs.cleanup_all()
    assert not first_dir.exists() and not second_dir.exists()


def test_cancel_invalidates_callbacks_and_cleans_only_its_directory(tmp_path):
    runs = ClipRunManager(tmp_path)
    generation, old_dir = runs.begin()
    runs.cancel()
    next_generation, new_dir = runs.begin()

    assert not runs.is_current(generation)
    assert runs.is_current(next_generation)
    runs.cleanup(old_dir)
    assert not old_dir.exists()
    assert new_dir.is_dir()

    runs.cleanup_all()
    assert not new_dir.exists()
