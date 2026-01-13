# Emergency Stop and Overspeed Handling

This document describes the emergency stop and overspeed detection and clearing functionality added to CodroidAPI based on HAR file analysis.

## Overview

The CodroidAPI now provides comprehensive emergency handling capabilities:

1. **Detection**: Automatically detect emergency stop and overspeed conditions from incoming websocket messages
2. **Clearing**: Execute the proper recovery sequence to clear error conditions
3. **Monitoring**: Continuously monitor for error conditions with optional auto-recovery
4. **Integration**: Easy integration into production workflows

## Implementation Details

Based on the analysis of `overspeed-and-emergency-stop.har`, the following patterns were identified:

### Message Format
Robot status messages use websocket communication with JSON format:
```json
{
  "id": "ws...",
  "time": 1234567890,
  "token": "user:admin", 
  "type": "common",
  "action": "setparam",
  "data": [{"path": "Robot/Control/command", "value": <command_code>}]
}
```

### Command Codes
- `0` = stop command
- `1` = power_on
- `2` = power_off
- `3` = manual_mode
- `5` = auto_mode

### Recovery Sequence
The standard recovery sequence observed in the HAR file:
1. Stop command (`0`)
2. Power off (`2`) 
3. Power on (`1`)
4. Restore auto mode (`5`)

## API Reference

### Detection Methods

#### `detect_emergency_stop(message: Dict[str, Any]) -> bool`
Detects emergency stop conditions in websocket messages.

```python
async def example_detection():
    async for message in client.listen():
        if await client.detect_emergency_stop(message):
            print("Emergency stop detected!")
            break
```

#### `detect_overspeed(message: Dict[str, Any]) -> bool`
Detects overspeed conditions in websocket messages.

```python
is_overspeed = await client.detect_overspeed(message)
```

#### `detect_joint_protection(message: Dict[str, Any]) -> bool`
Detects joint collision/protection warnings.

```python
if await client.detect_joint_protection(message):
    print("Joint protection warning detected!")
```

#### `detect_wrong_tool_error(message: Dict[str, Any]) -> bool`
Detects wrong tool/payload errors reported via `RobotError`.

```python
if await client.detect_wrong_tool_error(message):
    print("Tool/payload mismatch detected!")
```

#### `detect_drag_not_allowed(message: Dict[str, Any]) -> bool`
Detects the drag-not-allowed warning (`errorCode = 269485573`) that the UI raises when
external forces or payload/tool settings prevent motion.

```python
if await client.detect_drag_not_allowed(message):
    print("Drag not allowed warning raised!")
```

### Clearing Methods

#### `clear_emergency_stop() -> None`
Executes the recovery sequence to clear emergency stop condition.

```python
await client.clear_emergency_stop()
```

#### `clear_overspeed() -> None` 
Executes the recovery sequence to clear overspeed condition.

```python
await client.clear_overspeed()
```

#### `clear_joint_protection() -> None`
Clears joint protection/collision alarms with the standard recovery flow.

```python
await client.clear_joint_protection()
```

#### `clear_tool_error() -> None`
Runs the recovery flow whenever the wrong tool/payload error is detected.

```python
await client.clear_tool_error()
```

#### `clear_drag_not_allowed() -> None`
Issues the UI clear error command (`Robot/Control/command = 501`) to acknowledge a
drag-not-allowed warning once any external force is removed.

```python
await client.clear_drag_not_allowed()
```

### Rescue Controls

#### `enter_rescue_mode() -> None`
Ensures the robot is powered off before issuing the Rescue button command captured in the HAR (`Robot/Control/command = 4`).

```python
await client.enter_rescue_mode()
```

#### `exit_rescue_mode() -> None`
Powers off the robot (command `2`) as the UI did after completing the rescue flow.

```python
await client.exit_rescue_mode()
```

### Monitoring Methods

#### `monitor_robot_errors() -> AsyncIterator[Dict[str, Any]]`
Continuously monitors for emergency, overspeed, joint protection, or wrong-tool errors.

```python
async for error_event in client.monitor_robot_errors():
    error_type = error_event["error_type"]  # "emergency_stop", "overspeed", "joint_protection", "drag_not_allowed", or "wrong_tool"
    print(f"Error detected: {error_type} at {error_event['timestamp']}")
```

#### `watch_rescue_mode() -> AsyncIterator[Dict[str, Any]]`
Yields rescue mode transitions whenever `RobotStatus.state` flips into/out of state `3`.

```python
async for event in client.watch_rescue_mode():
    print(f"Rescue mode {event['event']} at {event['timestamp']}")
```

#### `watch_drag_not_allowed() -> AsyncIterator[Dict[str, Any]]`
Tracks drag-not-allowed warning events so your tooling can react when motion is blocked.

```python
async for event in client.watch_drag_not_allowed():
    print(f"Drag warning {event['event']} at {event['timestamp']}")
```

#### `auto_recover_from_errors(monitor_duration=None, auto_clear=True) -> AsyncIterator[Dict[str, Any]]`
Monitors for errors with optional automatic recovery.

```python
# Monitor with auto-recovery for 60 seconds
async for event in client.auto_recover_from_errors(
    monitor_duration=60.0,
    auto_clear=True
):
    print(f"Error {event['error_type']}: {event['recovery_status']}")
```

## Usage Examples

### Basic Error Detection and Clearing

```python
import asyncio
from codroid_api.client import CodroidAPI, CodroidConfig

async def basic_example():
    config = CodroidConfig(host="192.168.101.100")
    
    async with CodroidAPI(config) as client:
        await client.ws_login_with_password()
        await client.robot_login()
        
        # Monitor for errors
        async for message in client.listen():
            if await client.detect_emergency_stop(message):
                print("Emergency stop detected - clearing...")
                await client.clear_emergency_stop()
                break
            
            if await client.detect_overspeed(message):
                print("Overspeed detected - clearing...")
                await client.clear_overspeed()
                break

asyncio.run(basic_example())
```

### Automated Error Recovery

```python
async def automated_recovery():
    config = CodroidConfig(host="192.168.101.100")
    
    async with CodroidAPI(config) as client:
        await client.ws_login_with_password()
        await client.robot_login()
        
        # Auto-recover from errors indefinitely
        async for event in client.auto_recover_from_errors(auto_clear=True):
            if event["recovery_status"] == "cleared":
                print(f"✅ Auto-recovered from {event['error_type']}")
            else:
                print(f"❌ Recovery failed: {event['recovery_status']}")

asyncio.run(automated_recovery())
```

### Production Workflow Integration

```python
async def production_workflow():
    config = CodroidConfig(host="192.168.101.100")
    
    async with CodroidAPI(config) as client:
        await client.ws_login_with_password()
        await client.robot_login()
        
        # Start error monitoring in background
        error_task = asyncio.create_task(background_error_monitor(client))
        
        try:
            # Your production code here
            await client.power_on()
            await client.set_auto_mode()
            await client.move_safe(hold_seconds=2.0)
            # ... more robot operations
            
        finally:
            error_task.cancel()

async def background_error_monitor(client):
    async for event in client.auto_recover_from_errors(auto_clear=True):
        logging.warning(f"Error auto-recovery: {event}")

asyncio.run(production_workflow())
```

## Testing

Run the test suite to verify functionality:

```bash
cd /path/to/codroid-api
python examples/test_emergency_handling.py
```

```bash
python examples/test_rescue_mode.py
```

For comprehensive examples:

```bash
python examples/emergency_handling.py
```

### Live Monitoring Scripts

Helper scripts under `examples/` mirror the HAR captures for fast verification:
- `examples/test_emergency_button.py` watches the emergency button flow from `emergency-button.har`.
- `examples/test_overspeed_joint_protection.py` monitors overspeed and joint protection warnings from `overspeed-and-joint-protection.har`.
- `examples/test_rescue_mode.py` reports wrong tool/payload errors and exposes the Rescue commands captured in `rescue.har` (`enter_rescue_mode`, `exit_rescue_mode`, `clear_tool_error`) plus an `on` command that powers the robot on.
- `examples/test_auto_mode_loop.py` exercises the automatic movement between candle and safe presets at 100% manual move rate so you can verify the auto workflow end-to-end.

Run them against a live connection when reproducing the recorded faults.

## Auto Mode Notes (Manual Motion)

Analysis of `auto.har` shows that the UI does not send manual move commands while in
auto mode. Instead, it switches to project execution and drives program steps:

- `projexecute.run` with `mode=2` (auto mode)
- Repeated `common.setparam` calls to `Instruction/Project/projectStepSignal = true`

This means manual motion is effectively blocked in auto mode by design. If you need
movement while the robot is in auto mode, run a project and advance it with the
project step signal rather than sending manual move commands.

## Error Detection Patterns

The detection methods look for various patterns in websocket messages:

### Emergency Stop Patterns
- `robot_status.emergency_stop = True`
- `robot_status.estop = True` 
- `alarms` array containing emergency indicators
- String content containing "emergency" and "stop"

### Overspeed Patterns
- `robot_status.overspeed = True`
- `robot_status.speed_alarm = True`
- `alarms` array containing overspeed indicators
- String content containing "overspeed" or "speed"

### Wrong Tool / Rescue Patterns
- `RobotError` entries with `errorCode = 269485337` and info mentioning payload/tool settings
- Rescue mode is represented by `RobotStatus.state == 3` and returns to `1` when canceled

### Drag Not Allowed Patterns
- `RobotWarning` entries with `errorCode = 269485573`
- Info text such as "Drag not allowed" or mentions of external force/payload tooling

## Configuration

The recovery sequences use configurable timing:
- `0.1s` delay between stop and power off
- `0.5s` delay after power off/on operations

These can be customized by modifying the `clear_emergency_stop()` and `clear_overspeed()` methods.

## Troubleshooting

### Common Issues

1. **Detection not working**: Verify message format matches expected patterns
2. **Recovery failing**: Check robot permissions and connection status
3. **Timeout errors**: Increase delay timing in recovery sequences

### Debugging

Enable debug logging to see message contents:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Monitor message structure
async for message in client.listen():
    print(f"Message: {message}")
    # Check detection results
```

## Contributing

When adding new error patterns:

1. Update the detection methods in `client.py`
2. Add test cases in `examples/test_emergency_handling.py`
3. Update this documentation
4. Run the test suite to verify compatibility
