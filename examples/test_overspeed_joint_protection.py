#!/usr/bin/env python3
"""
Live monitor for overspeed and joint protection warnings based on the HAR capture.
"""

import argparse
import asyncio
import logging
import os
from contextlib import suppress

from codroid_api.client import CodroidAPI, CodroidConfig

logger = logging.getLogger(__name__)


async def monitor_protection_events(api: CodroidAPI, stop_event: asyncio.Event, verbose: bool) -> None:
    """Watch for overspeed and joint protection warnings and log state transitions."""
    warning_state = {
        "overspeed": False,
        "joint_protection": False,
    }

    async for message in api.listen():
        if stop_event.is_set():
            break

        if message.get("action") != "RobotWarning":
            continue

        if verbose:
            logger.debug("RobotWarning payload: %s", message.get("data"))

        timestamp = message.get("time") or "<unknown>"
        overspeed = CodroidAPI.is_overspeed_warning(message)
        joint = CodroidAPI.is_joint_protection_warning(message)

        for name, active, label in (
            ("overspeed", overspeed, "⚡ Overspeed warning"),
            ("joint_protection", joint, "⚠️ Joint protection warning"),
        ):
            if active and not warning_state[name]:
                warning_state[name] = True
                logger.warning("%s detected at %s", label, timestamp)
            elif not active and warning_state[name]:
                warning_state[name] = False
                logger.info("%s cleared at %s", label, timestamp)


async def console_control(api: CodroidAPI, stop_event: asyncio.Event) -> None:
    """Listen for console commands and trigger the appropriate recoveries."""
    loop = asyncio.get_running_loop()
    logger.info("Commands: o/overspeed (clear overspeed) | j/joint (clear joint) | r/recover (run both) | q/quit")

    while not stop_event.is_set():
        try:
            user_input = await loop.run_in_executor(None, input)
        except (EOFError, KeyboardInterrupt):
            stop_event.set()
            break

        command = user_input.strip().lower()
        if not command:
            continue

        if command in ("q", "quit", "exit"):
            stop_event.set()
            break

        try:
            if command in ("o", "overspeed", "clear-overspeed"):
                logger.info("Clearing overspeed protection...")
                await api.clear_overspeed()
                logger.info("Overspeed recovery sequence finished.")
            elif command in ("j", "joint", "clear-joint"):
                logger.info("Clearing joint protection...")
                await api.clear_joint_protection()
                logger.info("Joint protection recovery sequence finished.")
            elif command in ("r", "recover", "clear-all"):
                logger.info("Running full recovery sequence (overspeed + joint)...")
                await api.clear_overspeed()
                await api.clear_joint_protection()
                logger.info("Full recovery finished.")
            else:
                logger.warning("Unknown command: %s", command)
        except Exception as exc:
            logger.error("Recovery command failed: %s", exc)


async def run_live_monitor() -> None:
    parser = argparse.ArgumentParser(description="Live overspeed and joint protection monitor")
    parser.add_argument("--host", default=os.getenv("CODROID_HOST", "192.168.101.100"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CODROID_PORT", "9000")),
        help="Websocket port (default 9000 for robot stream)",
    )
    parser.add_argument("--origin", default=os.getenv("CODROID_ORIGIN", "http://192.168.101.100:9098"))
    parser.add_argument("--token", default=os.getenv("CODROID_TOKEN", "user:admin"))
    parser.add_argument("--username", default=os.getenv("CODROID_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("CODROID_USER_PASSWORD", "123456"))
    parser.add_argument("--debug", action="store_true", help="Log full RobotWarning payloads")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = CodroidConfig(
        host=args.host,
        port=args.port,
        origin=args.origin,
        token=args.token,
        username=args.username,
        user_password=args.password,
    )

    async with CodroidAPI(config) as api:
        logger.info("Connecting to ws://%s:%s (origin=%s)", config.host, config.port, config.origin)
        await api.ws_login_with_password()
        await api.robot_login()
        logger.info("Requesting initial config/data (matches the UI capture)...")
        await api.read_config()
        await api.read_system_data()
        await api.read_global_data()
        await api.get_io_info()
        await api.set_language(api.config.default_language)
        logger.info("Powering on robot before monitoring...")
        await api.power_on()

        stop_event = asyncio.Event()
        monitor_task = asyncio.create_task(monitor_protection_events(api, stop_event, verbose=args.debug))
        control_task = asyncio.create_task(console_control(api, stop_event))

        logger.info("Overspeed/joint protection monitor running — enter a command to clear events.")
        await stop_event.wait()

        for task in (monitor_task, control_task):
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(monitor_task, control_task)


def main() -> None:
    try:
        asyncio.run(run_live_monitor())
    except KeyboardInterrupt:
        logger.info("Shutting down live monitor.")


if __name__ == "__main__":
    main()
