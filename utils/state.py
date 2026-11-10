"""Ephemeral flow state for prompts, confirmations and broadcast drafts.

State lives in memory only: a restart drops half-finished flows, which is the
behaviour you want for dialogs that end in a destructive action.
"""

import threading

_lock = threading.Lock()
_state = {}
_MAX_KEYS = 5000


def set_state(key, flow, **data):
    """Remember that `key` is in `flow`, with any extra payload."""
    entry = {"flow": flow}
    entry.update(data)
    with _lock:
        if len(_state) >= _MAX_KEYS and key not in _state:
            _state.clear()
        _state[key] = entry


def get_state(key):
    """The full state entry, or None."""
    with _lock:
        entry = _state.get(key)
        return dict(entry) if entry else None


def current_flow(key):
    """The flow name for `key`, or None."""
    entry = get_state(key)
    return entry["flow"] if entry else None


def clear_state(key):
    """Drop the state for `key`."""
    with _lock:
        _state.pop(key, None)


def pop_state(key):
    """Drop and return the state for `key`."""
    with _lock:
        return _state.pop(key, None)
