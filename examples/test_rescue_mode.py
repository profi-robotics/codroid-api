#!/usr/bin/env python3
"""
Live monitor for wrong tool/payload errors and Rescue mode operations.
"""

import argparse
import asyncio
import logging
import os
from contextlib import suppress

from codroid_api.client import CodroidAPI, CodroidConfig

logger = logging.getLogger(__name__)


async def monitor_tool_errors(api: CodroidAPI, stop_event: asyncio.Event, verbose: bool) -> None:
    """Watch for wrong tool/payload errors emitted through the websocket."""
    async for event in api.monitor_robot_errors():
        if stop_event.is_set():
            break

        if event.get("error_type") != "wrong_tool":
            continue

        message = event.get("message", {})
        timestamp = event.get("timestamp") or message.get("time") or "<unknown>"
        info = ""
        for payload in message.get("data", {}).get("data", []):
            if isinstance(payload, dict) and payload.get("info"):
                info = payload.get("info")
                break

        logger.error("Wrong tool/payload error detected at %s: %s", timestamp, info or "<no details>")
        if verbose:
            logger.debug("Full RobotError payload: %s", message)

        logger.info("Rescue commands are available below to address the issue.")


async def console_control(api: CodroidAPI, stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    logger.info(
        "Commands: e/enter (start Rescue) | x/exit (power off) |"
        " o/on (power on) | c/clear (tool recovery) | q/quit"
    )

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
            if command in ("e", "enter"):
                logger.info("Entering Rescue mode (command 4)...")
                await api.enter_rescue_mode()
                logger.info("Rescue mode command issued.")
            elif command in ("x", "exit"):
                logger.info("Exiting Rescue mode (power off)...")
                await api.exit_rescue_mode()
                logger.info("Power off command issued.")
            elif command in ("o", "on", "poweron"):
                logger.info("Powering on the robot...")
                await api.power_on()
                logger.info("Power on command issued.")
            elif command in ("c", "clear", "recover"):
                logger.info("Clearing tool/payload error sequence...")
                await api.clear_tool_error()
                logger.info("Tool error recovery sequence completed.")
            else:
                logger.warning("Unknown command: %s", command)
        except Exception as exc:
            logger.error("Recovery command failed: %s", exc)


async def run_live_monitor() -> None:
    parser = argparse.ArgumentParser(description="Live rescue mode monitor")
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
    parser.add_argument("--debug", action="store_true", help="Log RobotError payloads and debug info")
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
        logger.info("Requesting initial config/data...")
        await api.read_config()
        await api.read_system_data()
        await api.read_global_data()
        await api.get_io_info()
        await api.set_language(api.config.default_language)
        logger.info("Powering on robot to prepare for Rescue mode")
        await api.power_on()

        stop_event = asyncio.Event()
        monitor_task = asyncio.create_task(monitor_tool_errors(api, stop_event, verbose=args.debug))
        control_task = asyncio.create_task(console_control(api, stop_event))

        logger.info("Tool error monitor is running. Type a command above to interact.")
        await stop_event.wait()

        for task in (monitor_task, control_task):
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(monitor_task, control_task)


def main() -> None:
    try:
        asyncio.run(run_live_monitor())
    except KeyboardInterrupt:
        logger.info("Shutting down rescue monitor.")


if __name__ == "__main__":
    main()
