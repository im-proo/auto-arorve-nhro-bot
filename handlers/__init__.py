"""Handler registration.

Imports live inside register_all() so handler modules can import each other's
helpers without a partially initialised package.
"""


def register_all(bot):
    """Register every module's handlers on the shared bot instance."""
    from handlers import approve, broadcast, forcejoin, membership, owner, settings, start, stats

    start.register(bot)
    settings.register(bot)
    stats.register(bot)
    approve.register(bot)
    owner.register(bot)
    forcejoin.register(bot)
    broadcast.register(bot)
    membership.register(bot)
