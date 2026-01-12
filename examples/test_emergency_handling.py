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

    config = CodroidConfig()
    client = CodroidAPI(config)

    emergency_detected = 0
    overspeed_detected = 0

    for i, msg in enumerate(variations):
        is_emergency = await client.detect_emergency_stop(msg)
        is_overspeed = await client.detect_overspeed(msg)

        if is_emergency:
            emergency_detected += 1
            print(f"  Message {i+1}: Emergency detected ✅")
        if is_overspeed:
            overspeed_detected += 1
            print(f"  Message {i+1}: Overspeed detected ⚡")

    print(f"\nDetection summary:")
    print(f"  Emergency variations detected: {emergency_detected}/4 expected")
    print(f"  Overspeed variations detected: {overspeed_detected}/3 expected")


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
