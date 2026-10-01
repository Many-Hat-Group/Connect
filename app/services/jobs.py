"""Worker job entry points. Filled in by later phases."""


async def sweep() -> None:
    from . import lifecycle
    await lifecycle.sweep()


async def retention() -> None:
    from . import lifecycle
    await lifecycle.retention()


async def rustdesk_sync_if_due() -> None:
    from . import rustdesk
    await rustdesk.sync_if_due()
