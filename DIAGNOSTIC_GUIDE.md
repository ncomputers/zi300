# Camera Streaming Diagnostic Guide 🔍

This comprehensive toolkit helps you diagnose and fix camera streaming issues in your crowd management system.

## 🧰 Available Tools

| Tool | Purpose | Usage |
|------|---------|-------|
| `run_diagnostics.sh` | Full system diagnosis | `./run_diagnostics.sh` |
| `diagnose_streaming.py` | Python diagnostic script | `python3 diagnose_streaming.py` |
| `fix_streaming.py` | Automated fix application | `python3 fix_streaming.py --interactive` |
| `DIAGNOSTIC_GUIDE.md` | This comprehensive guide | You're reading it! |

**Quick Start**: Run `./run_diagnostics.sh` first to identify issues, then use `python3 fix_streaming.py --interactive` to fix them automatically.

## 🚀 Quick Start

### Option 1: Using the Shell Script (Recommended)
```bash
# Run full diagnostic
./run_diagnostics.sh

# Test specific camera
./run_diagnostics.sh --camera-id 1

# Test specific URL
./run_diagnostics.sh --url "rtsp://admin:password@192.168.1.100/stream"

# Verbose output
./run_diagnostics.sh --verbose
```

### Option 2: Direct Python Script
```bash
# Full diagnostic
python3 diagnose_streaming.py

# Specific camera
python3 diagnose_streaming.py --camera-id 1

# Custom URL
python3 diagnose_streaming.py --url "rtsp://camera/stream"
```

## 🧪 What the Diagnostic Tests

### 1. **Basic Connectivity**
- ✅ API server health check
- ✅ Redis connection
- ✅ Application status

### 2. **Camera Configuration**
- ✅ Camera list from database
- ✅ Camera enabled status
- ✅ Show flag (critical for streaming)
- ✅ URL validation

### 3. **Direct Camera Connection**
- ✅ FFmpeg connection test
- ✅ Network reachability
- ✅ Authentication validation
- ✅ Stream format compatibility

### 4. **Camera Test Endpoint**
- ✅ `/cameras/test` endpoint
- ✅ Single frame capture
- ✅ JPEG encoding

### 5. **Streaming Pipeline Components**
- ✅ **RtspConnector** - FFmpeg stream reader
- ✅ **FrameBus** - Frame buffer system
- ✅ **PreviewPublisher** - MJPEG stream generator

### 6. **API Streaming Endpoints**
- ✅ `/api/cameras/{id}/show` - Enable streaming
- ✅ `/api/cameras/{id}/stats` - Stream status
- ✅ `/api/cameras/{id}/mjpeg` - MJPEG stream

### 7. **Dashboard Integration**
- ✅ Dashboard page loads
- ✅ JavaScript files present
- ✅ Camera feed elements

## 🩺 Understanding Results

### ✅ **All Green** - Everything Works
Your streaming is properly configured! The issue might be:
- Browser cache (try Ctrl+F5)
- JavaScript errors (check browser console)
- Network connectivity

### ❌ **Red Results** - Issues Found

#### **Basic Connectivity Failed**
```
🚨 CRITICAL: API server is not responding
```
**Fix**: Start your application
```bash
python3 main.py
```

#### **Redis Connection Failed**
```
🚨 CRITICAL: Redis connection failed
```
**Fix**: Start Redis server
```bash
redis-server
# or
sudo systemctl start redis
```

#### **Camera Configuration Issues**
```
⚠️ Camera streaming is not enabled (show=false)
```
**Fix**: Enable streaming for camera
```bash
curl -X POST http://localhost:8000/api/cameras/{ID}/show
```

#### **Direct Camera Connection Failed**
```
❌ FFmpeg failed: Connection refused
```
**Fixes**:
- Check camera IP/URL: `ping 192.168.1.100`
- Test with VLC: Open `rtsp://admin:pass@192.168.1.100/stream`
- Verify credentials
- Check camera settings (enable RTSP)

#### **RTSP Connector Issues**
```
❌ RtspConnector error: Read timeout
```
**Fixes**:
- Try TCP transport: Add `?transport=tcp` to URL
- Lower resolution in camera settings
- Check network bandwidth
- Increase timeout values

## 🔧 Automated Fixes

### **Option 1: Auto-Fix Script (Recommended)**
```bash
# Interactive mode - guided fixes
python3 fix_streaming.py --interactive

# Fix all cameras automatically
python3 fix_streaming.py --all

# Fix specific camera
python3 fix_streaming.py --camera-id 1

# Clear Redis state and start fresh
python3 fix_streaming.py --clear-state
```

### **Option 2: Manual Fixes**

#### 1. **Enable Streaming for All Cameras**
```bash
# Get camera list
curl http://localhost:8000/api/camera_info

# Enable streaming for each camera
curl -X POST http://localhost:8000/api/cameras/1/show
curl -X POST http://localhost:8000/api/cameras/2/show
# ... repeat for each camera
```

### 2. **Check Application Logs**
```bash
tail -f logs/app.log
```
Look for:
- `STREAM_ERROR`
- `capture_error`
- `STREAM_RETRY`
- Connection failures

### 3. **Manual Camera Test**
```bash
# Test camera with FFmpeg directly
ffmpeg -rtsp_transport tcp -i "rtsp://admin:pass@192.168.1.100/stream" -frames:v 1 test.jpg

# Test with VLC
vlc rtsp://admin:pass@192.168.1.100/stream
```

### 4. **Reset Camera Configuration**
```bash
# Stop camera
curl -X POST http://localhost:8000/api/cameras/1/hide

# Restart camera stream
curl -X POST http://localhost:8000/api/cameras/1/reload

# Enable streaming
curl -X POST http://localhost:8000/api/cameras/1/show
```

### 5. **Restart Application Components**
```bash
# Full application restart
pkill -f main.py
python3 main.py

# Or restart specific services (if using systemd)
sudo systemctl restart your-app-name
```

## 🐛 Advanced Debugging

### Check Dashboard JavaScript Console
1. Open browser DevTools (F12)
2. Go to Console tab
3. Look for errors like:
   ```
   Failed to load resource: net::ERR_CONNECTION_REFUSED
   Mixed Content: The page at 'https://...' was loaded over HTTPS
   ```

### Monitor Network Traffic
1. Open DevTools → Network tab
2. Filter by "mjpeg" or "stream"
3. Check if requests are being made to `/api/cameras/{id}/mjpeg`

### Redis Debugging
```bash
# Connect to Redis CLI
redis-cli

# Check camera data
GET cameras
HGETALL "cam:1:state"

# Check if streams are enabled
HGETALL "preview_publisher"
```

### FFmpeg Command Debugging
The diagnostic will show the exact FFmpeg command used:
```
Command: ffmpeg -rtsp_transport tcp -i rtsp://***@192.168.1.100/stream -frames:v 1 -f mjpeg pipe:1
```

Test this command manually to isolate issues.

## 📊 Interpreting the Report

### Success Rate
- **100%**: Perfect setup ✅
- **80-99%**: Minor issues, mostly functional ⚠️
- **50-79%**: Significant problems, needs attention ❌
- **<50%**: Major configuration issues 🚨

### Common Patterns

#### **Pattern 1**: Test Works, API Fails
```
✅ Camera Test Endpoint
❌ API Streaming Endpoints
```
**Cause**: Streaming not enabled
**Fix**: Call `/api/cameras/{id}/show`

#### **Pattern 2**: Everything Works Except Dashboard
```
✅ All API tests pass
❌ Dashboard shows no video
```
**Cause**: JavaScript/frontend issue
**Fix**: Check browser console, clear cache

#### **Pattern 3**: Intermittent Failures
```
✅ Direct connection works
❌ RTSP Connector timeout
```
**Cause**: Network instability or overloaded camera
**Fix**: Reduce resolution, check network

## 📞 Getting Help

If the diagnostic doesn't solve your issue:

1. **Run with verbose output**:
   ```bash
   ./run_diagnostics.sh --verbose
   ```

2. **Save diagnostic output**:
   ```bash
   ./run_diagnostics.sh > diagnostic_report.txt 2>&1
   ```

3. **Check specific components**:
   - Application logs: `tail -f logs/app.log`
   - System logs: `journalctl -f -u your-service`
   - Network: `netstat -tulpn | grep 8000`

4. **Provide information**:
   - Diagnostic report
   - Camera make/model
   - Network topology
   - Error messages from logs

## 🎯 Quick Resolution Checklist

When camera test works but dashboard doesn't show streams:

- [ ] Run diagnostic tool: `./run_diagnostics.sh`
- [ ] Check if streaming enabled: `curl http://localhost:8000/api/cameras/1/stats`
- [ ] Enable streaming: `curl -X POST http://localhost:8000/api/cameras/1/show`
- [ ] Clear browser cache: Ctrl+F5
- [ ] Check browser console for errors
- [ ] Verify MJPEG endpoint: `curl http://localhost:8000/api/cameras/1/mjpeg`
- [ ] Restart application if needed

This comprehensive diagnostic tool should identify exactly where your streaming pipeline is breaking down! 🔍✨
