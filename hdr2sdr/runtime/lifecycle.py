"""Small, UI-independent state machine for one conversion run."""

from enum import Enum


class RunState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    ENCODING = "encoding"
    RESUMING = "resuming"
    CONCATENATING = "concatenating"
    STOPPED = "stopped"
    FAILED = "failed"
    DONE = "done"


class InvalidRunTransition(ValueError):
    pass


_ALLOWED = {
    RunState.IDLE: {RunState.STARTING},
    RunState.STARTING: {RunState.ENCODING, RunState.RESUMING, RunState.STOPPED, RunState.FAILED},
    RunState.ENCODING: {RunState.RESUMING, RunState.CONCATENATING, RunState.STOPPED,
                        RunState.FAILED, RunState.DONE},
    RunState.RESUMING: {RunState.ENCODING, RunState.CONCATENATING, RunState.STOPPED,
                        RunState.FAILED},
    RunState.CONCATENATING: {RunState.STOPPED, RunState.FAILED, RunState.DONE},
    RunState.STOPPED: {RunState.STARTING},
    RunState.FAILED: {RunState.STARTING},
    RunState.DONE: {RunState.STARTING},
}


class RunStateMachine:
    def __init__(self, initial: RunState = RunState.IDLE):
        self.state = RunState(initial)

    def transition(self, target: RunState) -> RunState:
        target = RunState(target)
        if target == self.state:
            return self.state
        if target not in _ALLOWED[self.state]:
            raise InvalidRunTransition(f"Cannot transition a run from {self.state.value} to {target.value}.")
        self.state = target
        return self.state


def transition_safely(machine: RunStateMachine, target: RunState, log) -> RunState:
    """Apply a Qt-slot transition without letting stale callbacks escape.

    The state machine remains strict for ordinary callers and tests. This
    adapter is specifically for UI callbacks that can be delivered twice or
    arrive after a run has already reached a terminal state.
    """
    target = RunState(target)
    if machine.state == RunState.IDLE and target != RunState.STARTING:
        machine.transition(RunState.STARTING)
        if target in (RunState.CONCATENATING, RunState.DONE):
            machine.transition(RunState.ENCODING)
    try:
        return machine.transition(target)
    except InvalidRunTransition as error:
        log(f"run state: {error}")
        machine.state = target
        return target
