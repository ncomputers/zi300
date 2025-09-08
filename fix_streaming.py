#!/usr/bin/env python3
"""
Camera Streaming Auto-Fix Tool
==============================

This script automatically applies common fixes for camera streaming issues
based on diagnostic results or manual intervention.

Usage:
    python fix_streaming.py [--camera-id ID] [--auto-fix] [--force]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from utils.redis import get_sync_client

    PROJECT_IMPORTS_OK = True
except ImportError:
    PROJECT_IMPORTS_OK = False


class StreamingFixer:
    """Automated camera streaming fix tool."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.redis_client = None

        if PROJECT_IMPORTS_OK:
            try:
                self.redis_client = get_sync_client()
            except Exception:
                pass

    def get_cameras(self) -> List[Dict[str, Any]]:
        """Get list of all cameras."""
        try:
            response = requests.get(f"{self.base_url}/api/camera_info", timeout=10)
            response.raise_for_status()
            return response.json().get("cameras", [])
        except Exception as e:
            print(f"❌ Failed to get cameras: {e}")
            return []

    def check_api_health(self) -> bool:
        """Check if API is responding."""
        try:
            response = requests.get(f"{self.base_url}/api/v1/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def enable_camera_streaming(self, camera_id: int) -> bool:
        """Enable streaming for a specific camera."""
        try:
            print(f"🔧 Enabling streaming for camera {camera_id}...")

            # Call show endpoint
            response = requests.post(f"{self.base_url}/api/cameras/{camera_id}/show", timeout=10)
            response.raise_for_status()

            # Verify it's enabled
            time.sleep(1)
            stats_response = requests.get(
                f"{self.base_url}/api/cameras/{camera_id}/stats", timeout=10
            )
            if stats_response.status_code == 200:
                stats = stats_response.json()
                if stats.get("preview"):
                    print(f"✅ Camera {camera_id} streaming enabled successfully")
                    return True
                else:
                    print(f"⚠️  Camera {camera_id} show called but preview still false")

        except Exception as e:
            print(f"❌ Failed to enable streaming for camera {camera_id}: {e}")

        return False

    def reload_camera_stream(self, camera_id: int) -> bool:
        """Reload/restart camera stream."""
        try:
            print(f"🔄 Reloading stream for camera {camera_id}...")

            response = requests.post(f"{self.base_url}/api/cameras/{camera_id}/reload", timeout=15)
            response.raise_for_status()

            result = response.json()
            if result.get("reloaded"):
                print(f"✅ Camera {camera_id} stream reloaded successfully")
                return True
            else:
                print(f"⚠️  Camera {camera_id} reload called but not confirmed")

        except Exception as e:
            print(f"❌ Failed to reload camera {camera_id}: {e}")

        return False

    def test_mjpeg_endpoint(self, camera_id: int) -> bool:
        """Test if MJPEG endpoint is working."""
        try:
            print(f"🧪 Testing MJPEG endpoint for camera {camera_id}...")

            response = requests.get(
                f"{self.base_url}/api/cameras/{camera_id}/mjpeg", timeout=10, stream=True
            )

            if response.status_code == 200:
                # Try to read some data
                chunk_count = 0
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        chunk_count += 1
                        if chunk_count >= 3:  # Read a few chunks
                            break

                if chunk_count > 0:
                    print(f"✅ Camera {camera_id} MJPEG endpoint working")
                    return True
                else:
                    print(f"❌ Camera {camera_id} MJPEG endpoint not producing data")
            else:
                print(f"❌ Camera {camera_id} MJPEG endpoint returned {response.status_code}")

        except Exception as e:
            print(f"❌ Camera {camera_id} MJPEG test failed: {e}")

        return False

    def fix_camera_configuration(self, camera_id: int) -> bool:
        """Fix common camera configuration issues."""
        try:
            cameras = self.get_cameras()
            camera = next((cam for cam in cameras if cam.get("id") == camera_id), None)

            if not camera:
                print(f"❌ Camera {camera_id} not found")
                return False

            print(
                f"🔧 Checking configuration for camera {camera_id} ({camera.get('name', 'Unknown')})..."
            )

            fixes_needed = []

            # Check if camera is enabled
            if not camera.get("enabled", False):
                fixes_needed.append("Camera is disabled")

            # Check if show flag is set
            if not camera.get("show", False):
                fixes_needed.append("Streaming not enabled (show=false)")

            # Check if URL is set
            if not camera.get("url"):
                fixes_needed.append("No camera URL configured")
                print(f"❌ Camera {camera_id} has no URL - please configure via web interface")
                return False

            if fixes_needed:
                print(f"⚠️  Issues found: {', '.join(fixes_needed)}")

                # Try to enable streaming (this might fix show=false)
                self.enable_camera_streaming(camera_id)

                return True
            else:
                print(f"✅ Camera {camera_id} configuration looks good")
                return True

        except Exception as e:
            print(f"❌ Failed to check camera {camera_id} configuration: {e}")
            return False

    def clear_redis_streaming_state(self) -> bool:
        """Clear Redis streaming state to reset everything."""
        if not self.redis_client:
            print("⚠️  Redis not available, skipping state clear")
            return False

        try:
            print("🧹 Clearing Redis streaming state...")

            # Clear preview publisher state
            keys_to_clear = [
                "preview_publisher:*",
                "camera_debug:*",
                "cam:*:state",
                "cam:*:watchdog",
            ]

            cleared_count = 0
            for pattern in keys_to_clear:
                keys = self.redis_client.keys(pattern)
                if keys:
                    self.redis_client.delete(*keys)
                    cleared_count += len(keys)

            if cleared_count > 0:
                print(f"✅ Cleared {cleared_count} Redis keys")
            else:
                print("✅ No Redis keys needed clearing")

            return True

        except Exception as e:
            print(f"❌ Failed to clear Redis state: {e}")
            return False

    def restart_application_services(self) -> bool:
        """Attempt to restart application services."""
        print("🔄 Attempting to restart application services...")

        # Try to find and restart the main process
        try:
            # Look for the main.py process
            result = subprocess.run(["pgrep", "-f", "main.py"], capture_output=True, text=True)

            if result.returncode == 0:
                pids = result.stdout.strip().split("\n")
                print(f"📋 Found {len(pids)} main.py processes")

                # Ask for confirmation unless in auto mode
                print("⚠️  This will restart the application. Continue? (y/N): ", end="")
                confirm = input().strip().lower()

                if confirm == "y":
                    for pid in pids:
                        if pid:
                            subprocess.run(["kill", "-TERM", pid])

                    print("🔄 Application processes terminated")
                    print("💡 Please restart the application manually: python3 main.py")
                    return True
                else:
                    print("❌ Application restart cancelled")
                    return False
            else:
                print("⚠️  No main.py processes found")
                return False

        except Exception as e:
            print(f"❌ Failed to restart services: {e}")
            return False

    def comprehensive_camera_fix(self, camera_id: int) -> bool:
        """Apply comprehensive fixes to a camera."""
        print(f"\n🔧 COMPREHENSIVE FIX - Camera {camera_id}")
        print("=" * 50)

        success_count = 0
        total_steps = 6

        # Step 1: Check configuration
        print("\n1️⃣ Checking camera configuration...")
        if self.fix_camera_configuration(camera_id):
            success_count += 1

        # Step 2: Enable streaming
        print("\n2️⃣ Ensuring streaming is enabled...")
        if self.enable_camera_streaming(camera_id):
            success_count += 1

        # Step 3: Reload stream
        print("\n3️⃣ Reloading camera stream...")
        if self.reload_camera_stream(camera_id):
            success_count += 1

        # Wait a moment for things to stabilize
        print("\n⏱️  Waiting for stream to stabilize...")
        time.sleep(3)

        # Step 4: Test MJPEG endpoint
        print("\n4️⃣ Testing MJPEG endpoint...")
        if self.test_mjpeg_endpoint(camera_id):
            success_count += 1

        # Step 5: Enable streaming again (in case it got disabled)
        print("\n5️⃣ Final streaming enable...")
        if self.enable_camera_streaming(camera_id):
            success_count += 1

        # Step 6: Final test
        print("\n6️⃣ Final verification...")
        time.sleep(2)
        if self.test_mjpeg_endpoint(camera_id):
            success_count += 1

        # Results
        print(f"\n📊 RESULTS: {success_count}/{total_steps} steps successful")

        if success_count >= 4:
            print(f"✅ Camera {camera_id} should now be working!")
            print(f"💡 Test it at: {self.base_url}")
        else:
            print(f"⚠️  Camera {camera_id} may still have issues")
            print("💡 Try running diagnostics for more details:")
            print(f"   ./run_diagnostics.sh --camera-id {camera_id}")

        return success_count >= 4

    def fix_all_cameras(self) -> None:
        """Apply fixes to all cameras."""
        print("\n🔧 FIXING ALL CAMERAS")
        print("=" * 30)

        cameras = self.get_cameras()
        if not cameras:
            print("❌ No cameras found")
            return

        print(f"📋 Found {len(cameras)} cameras")

        # Clear Redis state first
        self.clear_redis_streaming_state()

        fixed_count = 0
        for camera in cameras:
            camera_id = camera.get("id")
            if camera_id:
                print(f"\n{'─' * 40}")
                success = self.comprehensive_camera_fix(camera_id)
                if success:
                    fixed_count += 1

        print("\n📊 OVERALL RESULTS")
        print("=" * 20)
        print(f"Total cameras: {len(cameras)}")
        print(f"Fixed: {fixed_count}")
        print(f"Success rate: {(fixed_count/len(cameras)*100):.1f}%" if cameras else "0%")

        if fixed_count == len(cameras):
            print("\n🎉 All cameras should now be working!")
        elif fixed_count > 0:
            print(
                f"\n✅ {fixed_count} cameras fixed, {len(cameras) - fixed_count} may need manual intervention"
            )
        else:
            print("\n⚠️  No cameras could be automatically fixed")
            print("💡 Run diagnostics for detailed analysis:")
            print("   ./run_diagnostics.sh")

    def interactive_mode(self) -> None:
        """Interactive mode for guided fixing."""
        print("\n🔧 INTERACTIVE STREAMING FIX")
        print("=" * 30)

        if not self.check_api_health():
            print("❌ API not responding. Is the application running?")
            print("💡 Start with: python3 main.py")
            return

        cameras = self.get_cameras()
        if not cameras:
            print("❌ No cameras configured")
            print("💡 Add cameras via the web interface first")
            return

        print("\n📋 Available cameras:")
        for i, camera in enumerate(cameras, 1):
            status = "🟢" if camera.get("online") else "🔴"
            streaming = "📺" if camera.get("show") else "📵"
            print(
                f"  {i}. {status} {streaming} Camera {camera.get('id')} - {camera.get('name', 'Unknown')}"
            )

        print("\n🔧 Available actions:")
        print("  1. Fix specific camera")
        print("  2. Fix all cameras")
        print("  3. Clear Redis state")
        print("  4. Run diagnostics")
        print("  0. Exit")

        while True:
            try:
                choice = input("\nEnter choice (0-4): ").strip()

                if choice == "0":
                    print("👋 Goodbye!")
                    break
                elif choice == "1":
                    try:
                        cam_num = int(input("Enter camera number (1-{}): ".format(len(cameras))))
                        if 1 <= cam_num <= len(cameras):
                            camera_id = cameras[cam_num - 1]["id"]
                            self.comprehensive_camera_fix(camera_id)
                        else:
                            print("❌ Invalid camera number")
                    except ValueError:
                        print("❌ Invalid input")
                elif choice == "2":
                    confirm = (
                        input("Fix all cameras? This may take a while (y/N): ").strip().lower()
                    )
                    if confirm == "y":
                        self.fix_all_cameras()
                elif choice == "3":
                    confirm = input("Clear Redis streaming state? (y/N): ").strip().lower()
                    if confirm == "y":
                        self.clear_redis_streaming_state()
                elif choice == "4":
                    print("Running diagnostics...")
                    subprocess.run(["./run_diagnostics.sh"])
                else:
                    print("❌ Invalid choice")

            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Camera Streaming Auto-Fix Tool")
    parser.add_argument("--camera-id", type=int, help="Fix specific camera ID")
    parser.add_argument("--all", action="store_true", help="Fix all cameras")
    parser.add_argument("--clear-state", action="store_true", help="Clear Redis streaming state")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Application base URL")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    fixer = StreamingFixer(args.base_url)

    # Check API health first
    if not fixer.check_api_health():
        print("❌ Application not responding. Please start the application first:")
        print("   python3 main.py")
        sys.exit(1)

    try:
        if args.interactive or not any([args.camera_id, args.all, args.clear_state]):
            fixer.interactive_mode()
        elif args.clear_state:
            fixer.clear_redis_streaming_state()
        elif args.all:
            fixer.fix_all_cameras()
        elif args.camera_id:
            fixer.comprehensive_camera_fix(args.camera_id)

    except KeyboardInterrupt:
        print("\n👋 Fix operation cancelled by user")
    except Exception as e:
        print(f"\n🚨 Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
