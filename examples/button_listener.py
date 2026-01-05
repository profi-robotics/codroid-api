"""
Listen for hardware button presses (DI ports) and log state changes.

What we know from the HAR capture:
- `IOManager/GetIOInfo` enumerates DI names for the pendant and flange:
  DI32=modeSwitch, DI33=enableButton, DI40-43=flangeButton0-3, DI44-45=flangeDI0-1.
- No explicit button events were broadcast in the capture, so this script
  listens for any DI payloads that appear on the websocket stream and reports
  edges when they show up.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Dict

from codroid_api import CodroidAPI, CodroidSettings


async def poll_buttons() -> None:
    """Actively poll DI ports to detect button presses using CodroidAPI helper."""
    settings = CodroidSettings()
    robot_config = settings.build_robot_config()

    async with CodroidAPI(robot_config) as robot_api:
        await robot_api.robot_login()
        print("Listening for button edges (DI)... Ctrl+C to stop.")
        async for event in robot_api.watch_di_changes():
            label = event["label"]
            value = event["value"]
            port = event["port"]
            print(f"{label} (port {port}) -> {value}")


if __name__ == "__main__":
    # Default to polling mode to surface button changes immediately.
    asyncio.run(poll_buttons())
