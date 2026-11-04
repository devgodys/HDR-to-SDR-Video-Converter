import pytest

from hdr2sdr.runtime.workflow import (
    DolbyVisionAction, FinishAction, RunContext, RunWorkflow, StartAction,
)


def test_run_context_captures_only_stable_run_settings(tmp_path):
    context = RunContext()
    context.capture(
        tmp_path / "in.mkv", tmp_path / "out.mkv", "hdr10",
        {"encoder": "hevc", "src": "transient", "dst": "transient",
         "resume_seek_sec": 4, "handbrake_tool": "transient"})

    assert context.source == tmp_path / "in.mkv"
    assert context.destination == tmp_path / "out.mkv"
    assert context.kind == "hdr10"
    assert context.settings == {"encoder": "hevc"}


@pytest.mark.parametrize(
    ("process_busy", "setup_busy", "expected"),
    [(True, True, StartAction.WAIT_PROCESS),
     (False, True, StartAction.WAIT_SETUP),
     (False, False, None)],
)
def test_start_blocker_preserves_process_then_setup_priority(
        process_busy, setup_busy, expected):
    assert RunWorkflow.start_blocker(process_busy, setup_busy) == expected


@pytest.mark.parametrize(
    ("hb", "hb_available", "source", "destination", "expected"),
    [(True, False, False, False, StartAction.MISSING_BACKEND),
     (False, True, False, True, StartAction.MISSING_FILES),
     (False, True, True, False, StartAction.MISSING_FILES),
     (False, True, True, True, StartAction.CONTINUE)],
)
def test_start_input_decision_preserves_backend_then_file_priority(
        hb, hb_available, source, destination, expected):
    assert RunWorkflow.input_action(hb, hb_available, source, destination) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("unknown", StartAction.ANALYZE_SOURCE),
     ("sdr", StartAction.SKIP_SDR),
     ("hdr10", StartAction.CONTINUE)],
)
def test_source_classification_decision(kind, expected):
    assert RunWorkflow.source_action(kind) == expected


@pytest.mark.parametrize(
    ("profile", "kind", "handbrake", "engine", "placebo", "unattended", "expected"),
    [(5, "dolby", False, "cpu", True, False, DolbyVisionAction.BLOCK_PROFILE5),
     (5, "dolby", True, "gpu", True, False, DolbyVisionAction.BLOCK_PROFILE5),
     (5, "dolby", False, "gpu", True, True, DolbyVisionAction.ENABLE_PROFILE5_RPU),
     (None, "dolby", False, "cpu", False, True, DolbyVisionAction.WARN_QUEUE_BASE_LAYER),
     (None, "dolby", False, "cpu", False, False, DolbyVisionAction.CONFIRM_BASE_LAYER),
     (None, "hdr10", False, "cpu", False, False, DolbyVisionAction.CONTINUE)],
)
def test_dolby_vision_start_decision(
        profile, kind, handbrake, engine, placebo, unattended, expected):
    assert RunWorkflow.dolby_vision_action(
        profile, kind, using_handbrake=handbrake, tonemap_engine=engine,
        has_libplacebo=placebo, unattended=unattended) == expected


@pytest.mark.parametrize(
    ("result", "process_running", "queue_running", "expected"),
    [
        ("busy", False, True, "wait"),
        ("started", True, True, "none"),
        ("failed", False, True, "skip_queue_item"),
        ("failed", False, False, "none"),
    ],
)
def test_start_action_routes_busy_and_unstarted_queue_attempts(
        result, process_running, queue_running, expected):
    assert RunWorkflow.start_action(result, process_running, queue_running) == expected


@pytest.mark.parametrize(
    ("succeeded", "stopped", "resume", "part", "published", "expected"),
    [
        (True, False, True, True, None, FinishAction.CONCAT),
        (True, False, False, True, None, FinishAction.PUBLISH),
        (True, False, False, True, True, FinishAction.DONE),
        (True, False, False, True, False, FinishAction.RESUME),
        (True, False, False, False, None, FinishAction.DONE),
        (False, False, False, True, None, FinishAction.RESUME),
        (False, True, False, True, None, FinishAction.STOPPED),
    ],
)
def test_finish_action_routes_publish_resume_and_terminal_outcomes(
        succeeded, stopped, resume, part, published, expected):
    assert RunWorkflow.finish_action(
        succeeded, stopped, resume, part, part_published=published) == expected


def test_resume_offer_returns_to_active_workflow_or_a_terminal_outcome():
    assert RunWorkflow.after_resume_offer(False, True) == FinishAction.RESUME
    assert RunWorkflow.after_resume_offer(True, False) == FinishAction.STOPPED
    assert RunWorkflow.after_resume_offer(False, False) == FinishAction.FAILED


def test_concat_outcome_routes_to_success_or_terminal_state():
    assert RunWorkflow.concat_action(True, False) == FinishAction.DONE
    assert RunWorkflow.concat_action(False, True) == FinishAction.STOPPED
    assert RunWorkflow.concat_action(False, False) == FinishAction.FAILED
