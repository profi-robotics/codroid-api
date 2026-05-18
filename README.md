# Codroid API

Python client for the Codroid robot websocket protocol. The command and payload
helpers were derived from local controller browser captures; raw captures are
not stored in this repository.

Ukrainian version of this README:
https://github.com/profi-robotics/codroid-api/blob/main/README.uk.md

## Install (uv)

```bash
uv sync
```

Install the example apps (Dash) with:

```bash
uv sync --extra examples
```

## Configuration

Create a `.env` file (see `.env.example`) and fill in your robot-specific values.

```bash
cp .env.example .env
```

Required for most flows:

- `CODROID_TOKEN` (usually shaped like `user:<username>`)
- `CODROID_USERNAME`
- `CODROID_USER_PASSWORD` (or `CODROID_USERCODE`)
- `CODROID_DEFAULT_PROJECT`, `CODROID_DEFAULT_TASK`, `CODROID_DEFAULT_LABEL` (for `run_project`)

The defaults in `CodroidSettings` and `.env.example` are placeholders. Do not
commit real controller credentials, user codes, HAR files, cookies, or project
exports that contain site-specific data.

## Public Repository Safety

This client can issue real robot motion, mode, power, and project execution
commands. Treat all examples as live-motion examples:

- Run only on an isolated robot network that you are authorized to control.
- Keep `.env` local and untracked.
- Keep raw browser captures (`*.har`) out of git.
- Start with low speed and a cleared cell.
- Review scripts before running them; some examples intentionally move the robot.

## Usage

```python
import asyncio

from codroid_api import CodroidAPI, CodroidSettings


async def main() -> None:
    settings = CodroidSettings()
    user_config = settings.build_user_config()
    robot_config = settings.build_robot_config()

    async with CodroidAPI(user_config) as user_api, CodroidAPI(robot_config) as robot_api:
        await user_api.ws_login_with_password()
        await robot_api.robot_login()
        await robot_api.read_config()
        await robot_api.read_system_data()
        await robot_api.read_global_data()
        await robot_api.get_io_info()
        await robot_api.set_language(user_config.default_language)
        await robot_api.run_project()
        await robot_api.stop_project()

        message = await robot_api.recv(timeout=5)
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
```

The same flow is available as a runnable script:

```bash
uv run python examples/basic_usage.py
```

Point move loop example:

```bash
uv run python examples/point_loop.py
```

## Persistent session helper

Use `RobotSession` when you want a single long-lived user+robot websocket
with posture/DI monitoring managed for you (e.g., UI apps):

```python
from codroid_api import RobotSession


async def main() -> None:
    session = RobotSession()
    await session.connect("ws://codroid-controller.local:9000/")
    # Access the latest posture snapshot at any time.
    posture = session.position_snapshot()
    print("Posture:", posture.x, posture.y, posture.z)
    await session.close()
```

## Project and point helpers (HTTP)

The API now includes HTTP helpers to manage project files:

```python
# Create a fresh project with no points.
result = await api.create_project(label="NewProject")
project_id = result["id"]

# Add or update a point (provide the full point dict).
await api.upsert_point(project_id, {"label": "P1", "postype": "cpos", "data": {...}})

# Delete a point by id or label.
await api.delete_point(project_id, point_id="pt123")

# Read and save whole project documents directly.
project_doc = await api.read_project(project_id)
await api.save_project(project_doc)

# Delete a project (uses /robot/project/del?id=...).
await api.delete_project(project_id)
```

## Manual jog and point moves

The point-creation capture uses `jogMode/jogIndex/jogSpeed` for manual jogging and
`targetPosType/targetAPos/targetCPos` + command 106/107 for target moves:

```python
# Joint jog (axis 1, positive direction).
await robot_api.start_joint_jog(axis=1, direction=1)
await robot_api.send_command_heartbeat()
await robot_api.stop_jog()

# TCP jog in tool frame.
await robot_api.start_tcp_jog(axis=1, direction=-1, reference=robot_api.config.jog_references.tool)

# Joint target move (APOS/DAPOS).
apos = CodroidAPI.build_target_apos([j1, j2, j3, j4, j5, j6])
await robot_api.move_to_joint_target_linear(apos)

# Cartesian target move (CPOS/DCPOS) with posture control.
poscfg = CodroidAPI.build_poscfg(mode=4)
cpos = CodroidAPI.build_target_cpos(x, y, z, a, b, c, poscfg=poscfg)
await robot_api.move_to_cartesian_target_optimal(cpos)

# Coordinate calibration (3-point) tagged to user coordinate 5.
raw_points = [
    CodroidAPI.build_target_cpos(x1, y1, z1, a1, b1, c1),
    CodroidAPI.build_target_cpos(x2, y2, z2, a2, b2, c2),
    CodroidAPI.build_target_cpos(x3, y3, z3, a3, b3, c3),
]
calibrated_frame = await robot_api.coordinate_calibration(
    raw_points,
    coordinate_id=5,
    set_active_coordinate=True,
)
# Persist the frame into the user coordinate slot (matches HAR behavior).
await robot_api.change_coordinate_parameter(coordinate_id=5, frame=calibrated_frame)
print("Calibrated user frame:", calibrated_frame)

# See also: the interactive three-point workflow in examples/coordinate_calibration.py
await robot_api.close()
```

### Button/DI monitoring

The Web UI polls DI ports for pendant/flange buttons via `IOManager/GetIOValue`. You can stream DI edges directly:

```python
async with CodroidAPI(robot_config) as api:
    await api.robot_login()
    async for event in api.watch_di_changes():  # polls ports 0–15, 32, 33, 40–45
        print(event["label"], "->", event["value"])
```

See `examples/button_listener.py` for a runnable script.

### Move to a user-coordinate origin

To jog the robot to the origin of a calibrated user frame (e.g., coordinate ID 6):

```python
async with CodroidAPI(robot_config) as api:
    await api.robot_login()
    await api.move_to_coordinate_origin(
        coordinate_id=6,
        linear=True,          # or False for optimal-path move
        hold_seconds=5.0,     # send command heartbeats while motion executes
    )
```

## Inspect A Local Capture

List the unique actions captured in a local HAR file that is not committed:

```bash
uv run python examples/inspect_capture.py
```

## Replay A Local Capture

Use the capture loader to replay websocket sends from a local HAR file with
overrides for user fields:

```bash
uv run python examples/replay_capture.py
```

## OnRobot Integration (Provisional Mapping)

The API includes provisional OnRobot support for `2FG7`, `VGC10`, and
`Soft Gripper` through existing `common.setparam` transport paths.
These defaults can vary across controller firmware revisions.

```python
from codroid_api import (
    CodroidAPI,
    OnRobotModel,
    OnRobotProfile,
)

# Apply profile (model + payload + center-of-gravity + model params)
profile = OnRobotProfile(
    model=OnRobotModel.FG2_7,
    payload_kg=1.25,
    cog_x_m=0.0,
    cog_y_m=0.0,
    cog_z_m=0.08,
    params={"force_pct": 55, "speed_pct": 45, "width_mm": 25.0},
)
await robot_api.set_onrobot_profile(profile)

# Or set model/payload separately
await robot_api.set_onrobot_model(OnRobotModel.VGC10)
await robot_api.set_onrobot_payload(payload_kg=0.8, cog_x_m=0.0, cog_y_m=0.0, cog_z_m=0.05)
```

Runtime action helpers:

```python
# 2FG7
await robot_api.onrobot_2fg7_open(width_mm=70.0, speed_pct=50)
await robot_api.onrobot_2fg7_close(width_mm=5.0, force_pct=60, speed_pct=40)

# VGC10
await robot_api.onrobot_vgc10_vacuum_on(vacuum_pct=80, channel=1)
await robot_api.onrobot_vgc10_vacuum_off(channel=1)
await robot_api.onrobot_vgc10_blow_off(duration_ms=250, channel=1)

# Soft Gripper
await robot_api.onrobot_soft_gripper_grip(pressure_pct=50, duration_ms=300)
await robot_api.onrobot_soft_gripper_release(duration_ms=300)
```

## Notes

- Secrets and identifiers are loaded from `.env` via `CodroidSettings`.
- Add new command codes in `codroid_api/commands.py` and use `CodroidAPI.set_robot_command()` / `set_param()` to wire them.
- Responses arrive asynchronously; use `listen()` or `recv()` to consume messages.
- The API is only tested against Codroid Web UI v.1.6.3c (matching the captured HAR).
