import pytest

from hdr2sdr.runtime.lifecycle import InvalidRunTransition, RunState, RunStateMachine


def test_run_lifecycle_allows_encode_resume_concat_and_completion():
    lifecycle = RunStateMachine()
    for state in (
        RunState.STARTING,
        RunState.ENCODING,
        RunState.RESUMING,
        RunState.ENCODING,
        RunState.CONCATENATING,
        RunState.DONE,
    ):
        assert lifecycle.transition(state) == state


@pytest.mark.parametrize("terminal", [RunState.STOPPED, RunState.FAILED, RunState.DONE])
def test_run_lifecycle_can_start_a_new_run_after_terminal_state(terminal):
    lifecycle = RunStateMachine(RunState.ENCODING)
    lifecycle.transition(terminal)
    assert lifecycle.transition(RunState.STARTING) == RunState.STARTING


def test_run_lifecycle_rejects_impossible_transition():
    lifecycle = RunStateMachine()
    with pytest.raises(InvalidRunTransition):
        lifecycle.transition(RunState.CONCATENATING)
