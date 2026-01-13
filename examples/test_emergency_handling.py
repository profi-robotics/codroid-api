#!/usr/bin/env python3
"""
Simple test script for emergency stop and overspeed handling.
This script provides basic functionality testing without requiring a live robot connection.
"""

import asyncio
import json
from codroid_api.client import CodroidAPI, CodroidConfig

# Sample messages for testing (based on HAR analysis patterns)
SAMPLE_EMERGENCY_MESSAGE = {
    "type": "Robot",
    "action": "status",
    "data": {
        "robot_status": {
            "emergency_stop": True,
            "power": False
        },
        "alarms": [
            {"type": "emergency", "message": "Emergency stop activated"}
        ]
    }
}

SAMPLE_OVERSPEED_MESSAGE = {
    "type": "Robot",
    "action": "status",
    "data": {
        "robot_status": {
            "overspeed": True,
            "speed_alarm": True
        },
        "alarms": [
            {"type": "speed", "message": "Overspeed condition detected"}
        ]
    }
}

SAMPLE_NORMAL_MESSAGE = {
    "type": "Robot",
    "action": "status",
    "data": {
        "robot_status": {
            "power": True,
            "mode": "auto"
        }
    }
}

SAMPLE_TOOL_ERROR_MESSAGE = {
    "id": 0,
    "type": "publish",
    "code": 200,
    "msg": "",
    "action": "RobotError",
    "data": {
        "code": 0,
        "msg": "",
        "data": [
            {
                "errorCode": 269485337,
                "info": "Wrong payload or tool setting"
            }
        ]
    }
}

SAMPLE_JOINT_PROTECTION_MESSAGE = {
    "type": "Robot",
    "action": "RobotWarning",
    "data": {
        "code": 0,
        "msg": "",
        "data": [
            {
                "errorCode": 269485321,
                "info": "Joint collision detected"
            }
        ],
    },
}


async def test_error_detection():
    """Test the error detection methods without network connection."""
    print("Testing emergency stop and overspeed detection...")

    config = CodroidConfig()
    client = CodroidAPI(config)

    # Test emergency stop detection
    is_emergency = await client.detect_emergency_stop(SAMPLE_EMERGENCY_MESSAGE)
    print(
        f"Emergency stop detection: {'✅ PASS' if is_emergency else '❌ FAIL'}")

    # Test overspeed detection
    is_overspeed = await client.detect_overspeed(SAMPLE_OVERSPEED_MESSAGE)
    print(f"Overspeed detection: {'✅ PASS' if is_overspeed else '❌ FAIL'}")

    # Test joint protection detection
    is_joint = await client.detect_joint_protection(SAMPLE_JOINT_PROTECTION_MESSAGE)
    print(f"Joint protection detection: {'✅ PASS' if is_joint else '❌ FAIL'}")

    # Test wrong tool/payload detection
    is_tool_error = await client.detect_wrong_tool_error(SAMPLE_TOOL_ERROR_MESSAGE)
    print(f"Tool/payload error detection: {'✅ PASS' if is_tool_error else '❌ FAIL'}")

    # Test normal message (should not detect errors)
    is_emergency_normal = await client.detect_emergency_stop(SAMPLE_NORMAL_MESSAGE)
    is_overspeed_normal = await client.detect_overspeed(SAMPLE_NORMAL_MESSAGE)

    normal_pass = not is_emergency_normal and not is_overspeed_normal
    print(f"Normal message handling: {'✅ PASS' if normal_pass else '❌ FAIL'}")


async def test_command_methods():
    """Test that the clearing methods can be called (simulated)."""
    print("\nTesting command methods (simulation)...")

    config = CodroidConfig()
    client = CodroidAPI(config)

    # Mock the websocket connection for testing
    class MockWebSocket:
        async def send(self, data):
            message = json.loads(data)
            print(f"  📤 Would send: {message['action']} command")

    # Replace the websocket with our mock
    client._ws = MockWebSocket()

    print("Testing emergency stop clearing sequence:")
    try:
        # This will call the methods but send to our mock websocket
        await client.clear_emergency_stop()
        print("  ✅ Emergency stop clearing sequence completed")
    except Exception as e:
        print(f"  ❌ Emergency stop clearing failed: {e}")

    print("Testing overspeed clearing sequence:")
    try:
        await client.clear_overspeed()
        print("  ✅ Overspeed clearing sequence completed")
    except Exception as e:
        print(f"  ❌ Overspeed clearing failed: {e}")

    print("Testing joint protection clearing sequence:")
    try:
        await client.clear_joint_protection()
        print("  ✅ Joint protection clearing sequence completed")
    except Exception as e:
        print(f"  ❌ Joint protection clearing failed: {e}")

    print("Testing tool/payload clearing sequence:")
    try:
        await client.clear_tool_error()
        print("  ✅ Tool/payload clearing sequence completed")
    except Exception as e:
        print(f"  ❌ Tool/payload clearing failed: {e}")


async def test_message_structure():
    """Test various message structures to ensure robust detection."""
    print("\nTesting message structure variations...")

    # Test variations of emergency messages
    variations = [
        # Simple string-based detection
        {"data": "emergency stop condition"},

        # Nested structure
        {"data": {"status": {"emergency": True}}},

        # Array of alarms
        {"data": {"alarms": [{"description": "emergency stop"}]}},

        # Case insensitive
        {"data": "EMERGENCY STOP ACTIVATED"},

        # Overspeed variations
        {"data": "overspeed detected"},
        {"data": {"robot_status": {"speed_alarm": 1}}},
        {"data": {"alarms": [{"type": "overspeed"}]}},
    ]

    joint_variations = [
        {"data": "joint protection triggered"},
        {"data": {"alarms": [{"description": "Joint collision detected"}]}},
        {
            "type": "Robot",
            "action": "RobotWarning",
            "data": {
                "code": 0,
                "msg": "",
                "data": [
                    {
                        "errorCode": 269485321,
                        "info": "Joint collision protection triggered",
                    }
                ],
            },
        },
    ]

    error_variations = [
        {
            "action": "RobotError",
            "data": {
                "code": 0,
                "msg": "",
                "data": [
                    {
                        "errorCode": 269485337,
                        "info": "Wrong payload or tool setting",
                    }
                ],
            },
        },
        {"action": "RobotError", "data": {"data": [{"info": "Wrong tool setting"}]}},
    ]

    config = CodroidConfig()
    client = CodroidAPI(config)

    emergency_detected = 0
    overspeed_detected = 0
    joint_detected = 0
    tool_detected = 0

    for i, msg in enumerate(variations):
        is_emergency = await client.detect_emergency_stop(msg)
        is_overspeed = await client.detect_overspeed(msg)
        is_joint = await client.detect_joint_protection(msg)
        is_tool = await client.detect_wrong_tool_error(msg)

        if is_emergency:
            emergency_detected += 1
            print(f"  Message {i+1}: Emergency detected ✅")
        if is_overspeed:
            overspeed_detected += 1
            print(f"  Message {i+1}: Overspeed detected ⚡")
        if is_joint:
            joint_detected += 1
            print(f"  Message {i+1}: Joint protection detected ⚠️")
        if is_tool:
            tool_detected += 1
            print(f"  Message {i+1}: Tool/payload error detected 🛠️")

    base_index = len(variations)
    for offset, msg in enumerate(joint_variations, start=1):
        is_joint = await client.detect_joint_protection(msg)
        if is_joint:
            joint_detected += 1
            print(f"  Message {base_index + offset}: Joint protection detected ⚠️")

    error_base = base_index + len(joint_variations)
    for offset, msg in enumerate(error_variations, start=1):
        if await client.detect_wrong_tool_error(msg):
            tool_detected += 1
            print(f"  Message {error_base + offset}: Tool/payload error detected 🛠️")

    print(f"\nDetection summary:")
    print(f"  Emergency variations detected: {emergency_detected}/4 expected")
    print(f"  Overspeed variations detected: {overspeed_detected}/3 expected")
    print(f"  Joint variations detected: {joint_detected}/{len(joint_variations)} expected")
    print(f"  Tool/payload variations detected: {tool_detected}/{len(error_variations)} expected")


async def main():
    """Run all tests."""
    print("CodroidAPI Emergency Handling Test Suite")
    print("=" * 45)

    await test_error_detection()
    await test_command_methods()
    await test_message_structure()

    print("\n" + "=" * 45)
    print("Test suite completed!")
    print("\nNext steps:")
    print("1. Test with a live robot connection")
    print("2. Trigger actual emergency/overspeed conditions")
    print("3. Verify the clearing sequences work as expected")
    print("4. Integrate into your production workflow")

if __name__ == "__main__":
    asyncio.run(main())
