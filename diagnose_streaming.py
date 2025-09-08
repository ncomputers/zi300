#!/usr/bin/env python3
"""
Camera Streaming Diagnostic Tool
=================================

This script comprehensively tests the camera streaming pipeline to identify
issues preventing dashboard video feeds from working.

Usage:
    python diagnose_streaming.py [--camera-id ID] [--url RTSP_URL]
"""

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional
import argparse
import subprocess
import requests
import threading
from contextlib import contextmanager

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    import redis
    import numpy as np
    from loguru import logger
    
    # Import project components
    from modules.frame_bus import FrameBus
    from modules.preview.mjpeg_publisher import PreviewPublisher
    from modules.stream.rtsp_connector import RtspConnector
    from utils.redis import get_sync_client
    from utils.ffmpeg import build_snapshot_cmd
    from utils.url import mask_credentials
    from config import load_config
    
    PROJECT_IMPORTS_OK = True
except ImportError as e:
    print(f"⚠️  Project imports failed: {e}")
    print("Running in limited mode without project components")
    PROJECT_IMPORTS_OK = False


class StreamingDiagnostic:
    """Comprehensive streaming diagnostic tool."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
        self.test_results: Dict[str, Any] = {}
        self.redis_client = None
        self.config = {}
        
        # Initialize project components if available
        if PROJECT_IMPORTS_OK:
            try:
                self.redis_client = get_sync_client()
                self.config = self._load_project_config()
            except Exception as e:
                print(f"⚠️  Could not connect to Redis: {e}")

    def _load_project_config(self) -> Dict[str, Any]:
        """Load project configuration."""
        try:
            config_path = Path("config.json")
            if config_path.exists():
                with open(config_path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    @contextmanager
    def test_section(self, name: str):
        """Context manager for test sections."""
        print(f"\n{'='*60}")
        print(f"🔍 {name}")
        print('='*60)
        start_time = time.time()
        
        try:
            yield
            duration = time.time() - start_time
            print(f"✅ {name} - PASSED ({duration:.2f}s)")
            self.test_results[name] = {"status": "PASSED", "duration": duration}
        except Exception as e:
            duration = time.time() - start_time
            print(f"❌ {name} - FAILED ({duration:.2f}s)")
            print(f"   Error: {e}")
            self.test_results[name] = {"status": "FAILED", "error": str(e), "duration": duration}

    def test_basic_connectivity(self) -> None:
        """Test basic API connectivity."""
        with self.test_section("Basic API Connectivity"):
            response = requests.get(f"{self.base_url}/api/v1/health", timeout=10)
            response.raise_for_status()
            
            health_data = response.json()
            assert health_data.get("ok"), "Health check failed"
            print(f"   ✓ API is healthy: {health_data}")

    def test_redis_connection(self) -> None:
        """Test Redis connectivity."""
        with self.test_section("Redis Connection"):
            if not self.redis_client:
                raise Exception("Redis client not available")
            
            # Test basic Redis operations
            self.redis_client.ping()
            print("   ✓ Redis is responding")
            
            # Check for cameras in Redis
            cameras_data = self.redis_client.get("cameras")
            if cameras_data:
                cameras = json.loads(cameras_data)
                print(f"   ✓ Found {len(cameras)} cameras in Redis")
                for cam in cameras:
                    print(f"     - Camera {cam.get('id')}: {cam.get('name')} ({cam.get('url', 'No URL')})")
            else:
                print("   ⚠️  No cameras found in Redis")

    def get_cameras_list(self) -> List[Dict[str, Any]]:
        """Get list of cameras from API."""
        try:
            response = requests.get(f"{self.base_url}/api/camera_info", timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("cameras", [])
        except Exception as e:
            print(f"   ⚠️  Could not fetch cameras: {e}")
            return []

    def test_camera_configuration(self) -> List[Dict[str, Any]]:
        """Test camera configuration."""
        with self.test_section("Camera Configuration"):
            cameras = self.get_cameras_list()
            
            if not cameras:
                raise Exception("No cameras configured")
            
            print(f"   ✓ Found {len(cameras)} configured cameras")
            
            for cam in cameras:
                cam_id = cam.get('id')
                name = cam.get('name', 'Unknown')
                url = cam.get('url', 'No URL')
                enabled = cam.get('enabled', False)
                show = cam.get('show', False)
                online = cam.get('online', False)
                
                print(f"\n   Camera {cam_id} ({name}):")
                print(f"     URL: {mask_credentials(url) if url else 'Not set'}")
                print(f"     Enabled: {enabled}")
                print(f"     Show: {show}")
                print(f"     Online: {online}")
                
                if cam.get('stream_error'):
                    print(f"     ⚠️  Stream Error: {cam['stream_error']}")
                
                if not enabled:
                    print(f"     ⚠️  Camera {cam_id} is disabled")
                
                if not show:
                    print(f"     ⚠️  Camera {cam_id} streaming is not enabled (show=false)")
            
            return cameras

    def test_camera_direct_connection(self, url: str, cam_id: int = None) -> None:
        """Test direct camera connection using FFmpeg."""
        with self.test_section(f"Direct Camera Connection ({mask_credentials(url)})"):
            if not url:
                raise Exception("No camera URL provided")
            
            # Build FFmpeg snapshot command
            cmd = build_snapshot_cmd(url, "tcp")
            print(f"   Command: {mask_credentials(' '.join(cmd))}")
            
            # Execute FFmpeg command with timeout
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                    check=False
                )
                
                if result.returncode == 0 and result.stdout:
                    print(f"   ✅ FFmpeg capture successful ({len(result.stdout)} bytes)")
                    return True
                else:
                    error_msg = result.stderr.decode('utf-8', errors='ignore')[-500:]
                    print(f"   ❌ FFmpeg failed (code: {result.returncode})")
                    print(f"   Error: {error_msg}")
                    raise Exception(f"FFmpeg failed: {error_msg}")
                    
            except subprocess.TimeoutExpired:
                raise Exception("FFmpeg timeout - camera not responding")

    def test_camera_test_endpoint(self, url: str) -> None:
        """Test the camera test endpoint."""
        with self.test_section(f"Camera Test Endpoint ({mask_credentials(url)})"):
            test_data = {
                "url": url,
                "transport": "tcp",
                "timeout": 10
            }
            
            response = requests.post(
                f"{self.base_url}/cameras/test",
                json=test_data,
                timeout=20
            )
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'image/jpeg' in content_type:
                    print(f"   ✅ Test endpoint returned JPEG image ({len(response.content)} bytes)")
                else:
                    print(f"   ⚠️  Unexpected content type: {content_type}")
            else:
                error_data = response.json() if response.headers.get('content-type') == 'application/json' else {}
                error_msg = error_data.get('error', f'HTTP {response.status_code}')
                raise Exception(f"Test endpoint failed: {error_msg}")

    def test_rtsp_connector(self, url: str, cam_id: int) -> RtspConnector:
        """Test RtspConnector component."""
        with self.test_section(f"RTSP Connector Test (Camera {cam_id})"):
            if not PROJECT_IMPORTS_OK:
                raise Exception("Project components not available")
            
            # Create connector
            connector = RtspConnector(url, 640, 480, camera_id=cam_id)
            subscriber_queue = connector.subscribe(maxsize=2)
            
            print(f"   ✓ RtspConnector created")
            print(f"   Initial state: {connector.state}")
            
            # Start connector
            connector.start()
            print(f"   ✓ RtspConnector started")
            
            # Wait for connection and frames
            start_time = time.time()
            timeout = 15
            frame_received = False
            
            while time.time() - start_time < timeout:
                try:
                    frame = subscriber_queue.get(block=True, timeout=1.0)
                    if frame is not None:
                        print(f"   ✅ First frame received: {frame.shape}")
                        frame_received = True
                        break
                except:
                    pass
                
                stats = connector.stats()
                print(f"   Status: {stats['state']}, FPS: {stats['fps_in']}, Error: {stats.get('last_error', 'None')}")
                
                if stats['state'] == 'ERROR':
                    raise Exception(f"RtspConnector error: {stats['last_error']}")
            
            if not frame_received:
                stats = connector.stats()
                raise Exception(f"No frames received in {timeout}s. State: {stats['state']}, Error: {stats.get('last_error', 'Unknown')}")
            
            return connector

    def test_frame_bus_integration(self, connector: RtspConnector) -> FrameBus:
        """Test FrameBus integration with RtspConnector."""
        with self.test_section("FrameBus Integration"):
            if not PROJECT_IMPORTS_OK:
                raise Exception("Project components not available")
            
            # Create FrameBus and forward frames
            bus = FrameBus()
            subscriber_queue = connector.subscribe(maxsize=2)
            
            def frame_forwarder():
                """Forward frames from connector to bus."""
                while True:
                    try:
                        frame = subscriber_queue.get(timeout=1.0)
                        if frame is not None:
                            bus.put(frame)
                    except:
                        break
            
            # Start forwarder thread
            forwarder_thread = threading.Thread(target=frame_forwarder, daemon=True)
            forwarder_thread.start()
            
            print("   ✓ FrameBus created and forwarder started")
            
            # Test frame retrieval
            try:
                frame = asyncio.run(bus.get_latest_async(5000))  # 5 second timeout
                if frame is not None:
                    print(f"   ✅ Frame retrieved from FrameBus: {frame.shape}")
                else:
                    raise Exception("No frame available in FrameBus")
            except Exception as e:
                raise Exception(f"FrameBus frame retrieval failed: {e}")
            
            return bus

    def test_preview_publisher(self, cam_id: int, frame_bus: FrameBus) -> None:
        """Test PreviewPublisher component."""
        with self.test_section(f"PreviewPublisher Test (Camera {cam_id})"):
            if not PROJECT_IMPORTS_OK:
                raise Exception("Project components not available")
            
            # Create PreviewPublisher
            buses = {cam_id: frame_bus}
            publisher = PreviewPublisher(buses)
            
            print("   ✓ PreviewPublisher created")
            
            # Enable streaming for camera
            publisher.start_show(cam_id)
            print(f"   ✓ Streaming enabled for camera {cam_id}")
            
            # Test if showing
            is_showing = publisher.is_showing(cam_id)
            print(f"   Showing status: {is_showing}")
            
            if not is_showing:
                raise Exception("Camera streaming not enabled in PreviewPublisher")
            
            # Test stream generation (get a few frames)
            async def test_stream():
                frame_count = 0
                async for chunk in publisher.stream(cam_id):
                    frame_count += 1
                    print(f"   ✓ Received MJPEG chunk {frame_count} ({len(chunk)} bytes)")
                    if frame_count >= 3:  # Get 3 frames then stop
                        break
                return frame_count > 0
            
            stream_success = asyncio.run(test_stream())
            if not stream_success:
                raise Exception("PreviewPublisher stream generation failed")

    def test_api_streaming_endpoints(self, cam_id: int) -> None:
        """Test API streaming endpoints."""
        with self.test_section(f"API Streaming Endpoints (Camera {cam_id})"):
            
            # Test show endpoint
            response = requests.post(f"{self.base_url}/api/cameras/{cam_id}/show")
            if response.status_code == 200:
                show_data = response.json()
                print(f"   ✓ Show endpoint: {show_data}")
            else:
                print(f"   ⚠️  Show endpoint failed: {response.status_code}")
            
            # Test stats endpoint
            response = requests.get(f"{self.base_url}/api/cameras/{cam_id}/stats")
            if response.status_code == 200:
                stats = response.json()
                print(f"   ✓ Camera stats: preview={stats.get('preview')}, online={stats.get('online')}")
                
                if not stats.get('preview'):
                    print("   ⚠️  Preview not enabled in stats")
            else:
                print(f"   ⚠️  Stats endpoint failed: {response.status_code}")
            
            # Test MJPEG endpoint (just check if it responds)
            try:
                response = requests.get(
                    f"{self.base_url}/api/cameras/{cam_id}/mjpeg",
                    timeout=10,
                    stream=True
                )
                
                if response.status_code == 200:
                    content_type = response.headers.get('content-type', '')
                    if 'multipart/x-mixed-replace' in content_type:
                        print("   ✅ MJPEG endpoint is responding with correct content type")
                        
                        # Read a small amount of data to verify it's actually streaming
                        chunk_count = 0
                        for chunk in response.iter_content(chunk_size=1024):
                            if chunk:
                                chunk_count += 1
                                if chunk_count >= 5:  # Read a few chunks
                                    break
                        
                        if chunk_count > 0:
                            print(f"   ✅ MJPEG stream is producing data ({chunk_count} chunks received)")
                        else:
                            raise Exception("MJPEG stream not producing data")
                    else:
                        raise Exception(f"Wrong content type: {content_type}")
                else:
                    raise Exception(f"MJPEG endpoint returned {response.status_code}")
                    
            except requests.exceptions.RequestException as e:
                raise Exception(f"MJPEG endpoint connection failed: {e}")

    def test_dashboard_integration(self) -> None:
        """Test dashboard page and JavaScript integration."""
        with self.test_section("Dashboard Integration"):
            
            # Test dashboard page loads
            response = requests.get(f"{self.base_url}/", timeout=10)
            response.raise_for_status()
            
            html_content = response.text
            print("   ✓ Dashboard page loads successfully")
            
            # Check for required JavaScript files
            required_js = ["mjpeg_feed.js", "chart"]
            for js_file in required_js:
                if js_file in html_content:
                    print(f"   ✓ Found {js_file} reference")
                else:
                    print(f"   ⚠️  Missing {js_file} reference")
            
            # Check for camera feed elements
            if "feed-img" in html_content:
                print("   ✓ Camera feed elements found")
            else:
                print("   ⚠️  No camera feed elements found")

    def generate_report(self) -> None:
        """Generate comprehensive diagnostic report."""
        print(f"\n{'='*60}")
        print("📊 DIAGNOSTIC REPORT")
        print('='*60)
        
        passed = sum(1 for result in self.test_results.values() if result["status"] == "PASSED")
        failed = sum(1 for result in self.test_results.values() if result["status"] == "FAILED")
        total = len(self.test_results)
        
        print(f"Tests Run: {total}")
        print(f"Passed: {passed} ✅")
        print(f"Failed: {failed} ❌")
        print(f"Success Rate: {(passed/total)*100:.1f}%" if total > 0 else "0%")
        
        print(f"\n📋 Detailed Results:")
        for test_name, result in self.test_results.items():
            status_icon = "✅" if result["status"] == "PASSED" else "❌"
            duration = result.get("duration", 0)
            print(f"  {status_icon} {test_name} ({duration:.2f}s)")
            
            if result["status"] == "FAILED":
                error = result.get("error", "Unknown error")
                print(f"      Error: {error}")
        
        # Generate recommendations
        self._generate_recommendations()

    def _generate_recommendations(self) -> None:
        """Generate recommendations based on test results."""
        print(f"\n💡 RECOMMENDATIONS:")
        
        failed_tests = [name for name, result in self.test_results.items() if result["status"] == "FAILED"]
        
        if not failed_tests:
            print("   🎉 All tests passed! Your streaming setup is working correctly.")
            return
        
        if "Basic API Connectivity" in failed_tests:
            print("   🚨 CRITICAL: API server is not responding")
            print("      - Check if the application is running")
            print("      - Verify the base URL is correct")
            print("      - Check network connectivity")
        
        if "Redis Connection" in failed_tests:
            print("   🚨 CRITICAL: Redis connection failed")
            print("      - Ensure Redis server is running")
            print("      - Check Redis URL in configuration")
            print("      - Verify Redis credentials if using authentication")
        
        if "Camera Configuration" in failed_tests:
            print("   ⚠️  Camera configuration issues detected")
            print("      - Add cameras via the web interface")
            print("      - Ensure cameras have 'enabled': true and 'show': true")
            print("      - Verify camera URLs are correct")
        
        if any("Direct Camera Connection" in test for test in failed_tests):
            print("   ⚠️  Camera connectivity issues")
            print("      - Check camera network connectivity")
            print("      - Verify RTSP URL and credentials")
            print("      - Test camera with external tools (VLC, ffplay)")
            print("      - Check firewall settings")
        
        if any("RTSP Connector" in test for test in failed_tests):
            print("   ⚠️  RTSP Connector issues")
            print("      - Check FFmpeg installation")
            print("      - Review application logs for detailed errors")
            print("      - Try different transport methods (TCP vs UDP)")
        
        if any("API Streaming" in test for test in failed_tests):
            print("   ⚠️  API streaming pipeline issues")
            print("      - Call /api/cameras/{id}/show to enable streaming")
            print("      - Check if PreviewPublisher is properly initialized")
            print("      - Restart the application to reset streaming state")
        
        print(f"\n🔧 QUICK FIXES:")
        print("   1. Enable streaming for all cameras:")
        print("      curl -X POST http://localhost:8000/api/cameras/{ID}/show")
        print("   2. Check application logs:")
        print("      tail -f logs/app.log")
        print("   3. Restart application:")
        print("      python main.py")

    def run_full_diagnostic(self, camera_id: Optional[int] = None, camera_url: Optional[str] = None) -> None:
        """Run complete diagnostic suite."""
        print("🔍 CAMERA STREAMING DIAGNOSTIC TOOL")
        print("=" * 60)
        print(f"Target: {self.base_url}")
        
        # Basic connectivity tests
        self.test_basic_connectivity()
        
        if PROJECT_IMPORTS_OK:
            self.test_redis_connection()
        
        # Camera configuration
        cameras = self.test_camera_configuration()
        
        # If specific camera provided, test it; otherwise test first available
        test_camera = None
        test_url = camera_url
        
        if camera_id:
            test_camera = next((cam for cam in cameras if cam.get('id') == camera_id), None)
            if not test_camera:
                print(f"⚠️  Camera {camera_id} not found")
                return
        elif cameras:
            test_camera = cameras[0]
        
        if test_camera and not test_url:
            test_url = test_camera.get('url')
        
        if not test_url:
            print("⚠️  No camera URL available for testing")
            return
        
        cam_id = test_camera.get('id') if test_camera else 999
        
        # Core streaming tests
        self.test_camera_direct_connection(test_url, cam_id)
        self.test_camera_test_endpoint(test_url)
        
        # Component-level tests (only if project imports work)
        if PROJECT_IMPORTS_OK:
            try:
                connector = self.test_rtsp_connector(test_url, cam_id)
                frame_bus = self.test_frame_bus_integration(connector)
                self.test_preview_publisher(cam_id, frame_bus)
                
                # Cleanup
                connector.stop()
            except Exception as e:
                print(f"⚠️  Component tests failed: {e}")
        
        # API-level tests
        self.test_api_streaming_endpoints(cam_id)
        self.test_dashboard_integration()
        
        # Generate final report
        self.generate_report()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Camera Streaming Diagnostic Tool")
    parser.add_argument("--camera-id", type=int, help="Specific camera ID to test")
    parser.add_argument("--url", help="Specific camera URL to test")
    parser.add_argument("--base-url", default="http://localhost:8000", 
                       help="Base URL of the application")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.add(sys.stderr, level="DEBUG")
    
    diagnostic = StreamingDiagnostic(args.base_url)
    
    try:
        diagnostic.run_full_diagnostic(args.camera_id, args.url)
    except KeyboardInterrupt:
        print("\n⚠️  Diagnostic interrupted by user")
    except Exception as e:
        print(f"🚨 Diagnostic failed with unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()


if __name__ == "__main__":
    main()