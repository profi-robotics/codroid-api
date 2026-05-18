#!/usr/bin/env python3
"""
Live assistant for the emergency button flow.

Connects to the robot, monitors `RobotWarning` updates for the emergency-stop button,
and lets the operator clear the condition (full recovery sequence) with a console command.
"""

import asyncio
import logging
import argparse
import os
from contextlib import suppress

from codroid_api.client import CodroidAPI, CodroidConfig

logger = logging.getLogger(__name__)


async def monitor_emergency_button(api: CodroidAPI, verbose: bool = False) -> None:
    """Log button events after the session is ready."""
    emergency_active = False

    async for message in api.listen():
        if message.get("action") != "RobotWarning":
            continue

        if verbose:
            logger.debug("RobotWarning payload: %s", message.get("data"))

        if not emergency_active and CodroidAPI.is_emergency_button_warning(message):
            emergency_active = True
            timestamp = message.get("time") or "<unknown>"
            logger.info("Emergency button PRESSED at %s", timestamp)
        elif emergency_active and CodroidAPI.is_robot_warning_cleared(message):
            emergency_active = False
            timestamp = message.get("time") or "<unknown>"
            logger.info("Emergency button RELEASED at %s", timestamp)


async def console_control(api: CodroidAPI, stop_event: asyncio.Event) -> None:
    """Listen for console commands to clear the emergency state."""
    loop = asyncio.get_running_loop()
    logger.info("Commands: c/clear/recover (run recovery) | q/quit/exit (stop monitor)")
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
        if command in ("c", "clear", "recover"):
            logger.info("Triggering emergency recovery sequence...")
            try:
                await api.clear_emergency_stop()
                logger.info("Recovery sequence completed.")
            except Exception as exc:
                logger.error("Recovery sequence failed: %s", exc)


async def run_live_monitor() -> None:
    parser = argparse.ArgumentParser(description="Live emergency button monitor")
    parser.add_argument("--host", default=os.getenv("CODROID_HOST", "codroid-controller.local"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CODROID_PORT", "9000")),
        help="Websocket port (default 9000 for robot stream)",
    )
    parser.add_argument("--origin", default=os.getenv("CODROID_ORIGIN", "http://codroid-controller.local:9098"))
    parser.add_argument("--token", default=os.getenv("CODROID_TOKEN", "user:YOUR_USERNAME"))
    parser.add_argument("--username", default=os.getenv("CODROID_USERNAME", "YOUR_USERNAME"))
    parser.add_argument("--password", default=os.getenv("CODROID_USER_PASSWORD", ""))
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging of all RobotWarning payloads")
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
        monitor_task = asyncio.create_task(monitor_emergency_button(api, verbose=args.debug))
        control_task = asyncio.create_task(console_control(api, stop_event))

        logger.info("Emergency button monitor is running. Commands appear above; type one and press Enter.")
        await stop_event.wait()

        for task in (monitor_task, control_task):
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(monitor_task, control_task)


def main() -> None:
    """Entrypoint for the script."""
    try:
        asyncio.run(run_live_monitor())
    except KeyboardInterrupt:
        logger.info("Shutting down live monitor.")


if __name__ == "__main__":
    main()
