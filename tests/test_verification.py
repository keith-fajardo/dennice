import asyncio

from dennice.core.config import DenniceConfig
from dennice.core.verification import verify_executor, verify_router


def test_local_connection_checks_are_ready_without_network() -> None:
    async def check() -> None:
        config = DenniceConfig()
        router, executor = await asyncio.gather(verify_router(config), verify_executor(config))
        assert router.ok
        assert executor.ok

    asyncio.run(check())
