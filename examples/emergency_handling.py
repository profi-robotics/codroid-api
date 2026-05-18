#!/usr/bin/env python3
"""
Emergency Stop and Overspeed Handling Example

This example demonstrates how to:
1. Monitor for emergency stop and overspeed conditions
2. Manually detect and clear error conditions  
3. Set up automatic error recovery
4. Use the error monitoring in a production workflow

Based on the HAR file analysis of local_emergency_capture.har
"""

import asyncio
import logging
from codroid_api.client import CodroidAPI, CodroidConfig

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def manual_error_detection_example():
    """Example of manual error detection and clearing."""
    logger.info("=== Manual Error Detection Example ===")

    config = CodroidConfig(host="codroid-controller.local")

    async with CodroidAPI(config) as client:
        # Login
        await client.ws_login_with_password()
        await client.robot_login()

        logger.info("Monitoring for errors (manual detection)...")

        # Listen to a few messages and check for errors
        message_count = 0
        async for message in client.listen():
            message_count += 1

            # Check for emergency stop
            is_emergency = await client.detect_emergency_stop(message)
            if is_emergency:
                logger.warning("🚨 EMERGENCY STOP detected!")
                logger.info("Clearing emergency stop...")
                await client.clear_emergency_stop()
                logger.info("✅ Emergency stop cleared")
                break

            # Check for overspeed
            is_overspeed = await client.detect_overspeed(message)
            if is_overspeed:
                logger.warning("⚡ OVERSPEED detected!")
                logger.info("Clearing overspeed...")
                await client.clear_overspeed()
                logger.info("✅ Overspeed cleared")
                break

            # Stop after checking 10 messages if no errors found
            if message_count >= 10:
                logger.info("No errors detected in first 10 messages")
                break


async def automated_error_monitoring_example():
    """Example of automated error monitoring and recovery."""
    logger.info("=== Automated Error Recovery Example ===")

    config = CodroidConfig(host="codroid-controller.local")

    async with CodroidAPI(config) as client:
        # Login
        await client.ws_login_with_password()
        await client.robot_login()

        logger.info("Starting automated error monitoring for 30 seconds...")

        # Monitor for errors with automatic clearing for 30 seconds
        async for error_event in client.auto_recover_from_errors(
            monitor_duration=30.0,
            auto_clear=True
        ):
            error_type = error_event["error_type"]
            recovery_status = error_event["recovery_status"]
            timestamp = error_event["timestamp"]

            logger.warning(
                f"🔧 Error detected: {error_type} at {timestamp}, "
                f"recovery: {recovery_status}"
            )

            if recovery_status == "cleared":
                logger.info(f"✅ Successfully recovered from {error_type}")
            else:
                logger.error(f"❌ Failed to recover: {recovery_status}")


async def error_monitoring_only_example():
    """Example of error monitoring without automatic clearing."""
    logger.info("=== Error Monitoring Only Example ===")

    config = CodroidConfig(host="codroid-controller.local")

    async with CodroidAPI(config) as client:
        # Login
        await client.ws_login_with_password()
        await client.robot_login()

        logger.info("Monitoring errors without auto-clear for 15 seconds...")

        # Monitor for errors without automatic clearing
        async for error_event in client.auto_recover_from_errors(
            monitor_duration=15.0,
            auto_clear=False  # Only detect, don't clear
        ):
            error_type = error_event["error_type"]
            timestamp = error_event["timestamp"]
            message = error_event["message"]

            logger.warning(f"📊 Error detected: {error_type} at {timestamp}")
            logger.info(f"Message type: {message.get('type', 'unknown')}")
            logger.info(f"Message action: {message.get('action', 'unknown')}")

            # Manual decision on whether to clear
            logger.info(
                "Error detected but auto-clear disabled. Manual intervention required.")


async def production_workflow_example():
    """Example of using error handling in a production robot workflow."""
    logger.info("=== Production Workflow with Error Handling ===")

    config = CodroidConfig(host="codroid-controller.local")

    async with CodroidAPI(config) as client:
        # Login
        await client.ws_login_with_password()
        await client.robot_login()

        # Start error monitoring in the background
        error_monitor_task = asyncio.create_task(
            monitor_errors_background(client)
        )

        try:
            # Simulate a production workflow
            logger.info("Starting production workflow...")

            # Power on robot
            await client.power_on()
            await asyncio.sleep(1)

            # Set auto mode
            await client.set_auto_mode()
            await asyncio.sleep(0.5)

            # Simulate some robot movements
            logger.info("Moving to safe position...")
            await client.move_safe(hold_seconds=2.0)

            logger.info("Moving to home position...")
            await client.move_home(hold_seconds=2.0)

            logger.info("Production workflow completed successfully")

        except Exception as e:
            logger.error(f"Production workflow failed: {e}")

        finally:
            # Stop error monitoring
            error_monitor_task.cancel()
            try:
                await error_monitor_task
            except asyncio.CancelledError:
                pass

            logger.info("Error monitoring stopped")


async def monitor_errors_background(client: CodroidAPI):
    """Background task to monitor and auto-clear errors."""
    logger.info("Background error monitoring started")

    try:
        async for error_event in client.auto_recover_from_errors(auto_clear=True):
            error_type = error_event["error_type"]
            recovery_status = error_event["recovery_status"]

            if recovery_status == "cleared":
                logger.info(f"🔧 Auto-recovered from {error_type}")
            else:
                logger.error(
                    f"❌ Auto-recovery failed for {error_type}: {recovery_status}")

    except asyncio.CancelledError:
        logger.info("Background error monitoring cancelled")
    except Exception as e:
        logger.error(f"Error monitoring failed: {e}")


async def main():
    """Run all examples."""
    print("CodroidAPI Emergency Handling Examples")
    print("=====================================\n")

    # Run examples sequentially
    examples = [
        manual_error_detection_example,
        automated_error_monitoring_example,
        error_monitoring_only_example,
        production_workflow_example,
    ]

    for example in examples:
        try:
            await example()
            print("\n" + "="*50 + "\n")
        except Exception as e:
            logger.error(f"Example {example.__name__} failed: {e}")
            print("\n" + "="*50 + "\n")

        # Brief pause between examples
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
