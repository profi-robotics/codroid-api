#!/usr/bin/env python3
"""Move between candle and safe position in auto mode at 100% speed."""

import argparse
import asyncio
import logging
import os
import signal
from contextlib import suppress

from codroid_api.client import CodroidAPI, CodroidConfig

logger = logging.getLogger(__name__)


def _setup_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


async def _move_cycle(api: CodroidAPI, iterations: int, hold_seconds: float) -> None:
    logger.info("Setting manual move rate to 100%% and Auto mode")
    await api.set_manual_move_rate(1.0)
    await api.set_auto_mode()

    for index in range(1, iterations + 1):
        logger.info("[%02d/%02d] Moving to candle position", index, iterations)
        await api.move_candle(hold_seconds=hold_seconds)
        await asyncio.sleep(0.5)

        logger.info("[%02d/%02d] Moving to safe position", index, iterations)
        await api.move_safe(hold_seconds=hold_seconds)
        await asyncio.sleep(0.5)

    logger.info("Completed %d iterations", iterations)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-mode candle ↔ safe loop")
    parser.add_argument("--host", default=os.getenv("CODROID_HOST", "codroid-controller.local"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CODROID_PORT", "9000")))
    parser.add_argument("--origin", default=os.getenv("CODROID_ORIGIN", "http://codroid-controller.local:9098"))
    parser.add_argument("--token", default=os.getenv("CODROID_TOKEN", "user:YOUR_USERNAME"))
    parser.add_argument("--username", default=os.getenv("CODROID_USERNAME", "YOUR_USERNAME"))
    parser.add_argument("--password", default=os.getenv("CODROID_USER_PASSWORD", ""))
    parser.add_argument("--iterations", type=int, default=5, help="Number of candle/safe pairs")
    parser.add_argument("--hold", type=float, default=1.5, help="Seconds to hold each preset move")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    _setup_logging(args.debug)

    config = CodroidConfig(
        host=args.host,
        port=args.port,
        origin=args.origin,
        token=args.token,
        username=args.username,
        user_password=args.password,
    )

    async with CodroidAPI(config) as api:
        logger.info("Connecting to %s:%s", config.host, config.port)
        await api.ws_login_with_password()
        await api.robot_login()
        logger.info("Powering on robot")
        await api.power_on()

        stop_event = asyncio.Event()

        def _shutdown(*_: object) -> None:
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _shutdown)

        move_task = asyncio.create_task(_move_cycle(api, args.iterations, args.hold))

        await asyncio.wait({move_task}, return_when=asyncio.ALL_COMPLETED)
        stop_event.set()

        with suppress(asyncio.CancelledError):
            await move_task

        logger.info("Auto-mode candle loop finished")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted loop")
