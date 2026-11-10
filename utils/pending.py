"""Cache of join requests the bot has seen but not yet approved.

The Bot API has no method that lists pending join requests: requests only
arrive as `chat_join_request` updates. Remembering them here is what makes
/approveall and /declineall possible for requests seen since startup.
"""

import threading

_lock = threading.Lock()
_pending = {}
_MAX_PER_GROUP = 1000


def remember(group_id, user_id, first_name=None, username=None):
    with _lock:
        group = _pending.setdefault(group_id, {})
        if len(group) >= _MAX_PER_GROUP:
            group.pop(next(iter(group)), None)
        group[user_id] = {"first_name": first_name, "username": username}


def forget(group_id, user_id):
    with _lock:
        group = _pending.get(group_id)
        if not group:
            return
        group.pop(user_id, None)
        if not group:
            _pending.pop(group_id, None)


def get(group_id):
    """A copy of the pending requests for a group: {user_id: info}."""
    with _lock:
        return dict(_pending.get(group_id, {}))


def groups_for_user(user_id):
    """Every group id holding an unseen request from `user_id`."""
    with _lock:
        return [group_id for group_id, group in _pending.items() if user_id in group]


def clear(group_id):
    with _lock:
        _pending.pop(group_id, None)
