# Camera Streaming Diagnostic & Fix Tools 🔧

> **TL;DR**: Camera test works but dashboard streaming doesn't? Run `./run_diagnostics.sh` then `python3 fix_streaming.py --interactive`

## The Problem

You've experienced the classic issue where:
- ✅ Camera test/preview works in the admin interface
- ❌ Dashboard doesn't show live video streams
- ⚠️  "Stream unavailable" messages appear

This happens because the system uses **two different streaming approaches**:
1. **Camera Test** → Direct FFmpeg → Single frame (simple)
2. **Dashboard Streaming** → RtspConnector → FrameBus → PreviewPublisher → MJPEG Stream (complex)

## The Solution

We've built comprehensive diagnostic and fix tools that test **every component** in the streaming pipeline and automatically apply fixes.

## 🚀 Quick Fix

**Step 1: Diagnose**
```bash
./run_diagnostics.sh
```

**Step 2: Auto-Fix**  
```bash
python3 fix_streaming.py --interactive
```

**Step 3: Verify**
Check your dashboard - streaming should now work! 🎉

## 📋 Tool Overview

### 🔍 `run_diagnostics.sh` - Comprehensive System Diagnosis
- Tests API connectivity, Redis, camera configs
- Tests direct camera connection vs streaming pipeline
- Identifies exactly where the pipeline breaks
- Provides detailed recommendations

```bash
# Full system diagnosis
./run_diagnostics.sh

# Test specific camera  
./run_diagnostics.sh --camera-id 1

# Verbose output for troubleshooting
./run_diagnostics.sh --verbose
```

### 🔧 `fix_streaming.py` - Automated Problem Resolution
- Applies common fixes automatically
- Interactive guided mode for step-by-step fixes
- Can fix all cameras or specific ones
- Clears corrupted Redis state

```bash
# Interactive mode (recommended)
python3 fix_streaming.py --interactive

# Fix all cameras automatically
python3 fix_streaming.py --all

# Fix specific camera
python3 fix_streaming.py --camera-id 1
```

### 📊 `diagnose_streaming.py` - Advanced Python Diagnostics
- Core diagnostic engine (used by shell script)
- Tests individual components in isolation
- Provides programmatic access to diagnostics
- Can be imported and used in other tools

```bash
# Direct Python diagnostics
python3 diagnose_streaming.py --camera-id 1 --verbose
```

## 🎯 Common Scenarios

### Scenario 1: "All camera tests pass but no dashboard video"
**Root Cause**: Streaming not enabled  
**Fix**: 
```bash
python3 fix_streaming.py --all
```

### Scenario 2: "Some cameras work, others don't"
**Root Cause**: Mixed configuration states  
**Fix**:
```bash
./run_diagnostics.sh  # Identify which cameras
python3 fix_streaming.py --camera-id X  # Fix specific ones
```

### Scenario 3: "Everything worked, now nothing does"
**Root Cause**: Corrupted Redis state  
**Fix**:
```bash
python3 fix_streaming.py --clear-state
python3 fix_streaming.py --all
```

### Scenario 4: "Streaming works sometimes, fails other times"  
**Root Cause**: Network/connectivity issues  
**Fix**:
```bash
./run_diagnostics.sh --verbose  # Get detailed connection info
# Check camera network settings, reduce resolution
```

## 🩺 What Gets Tested

The diagnostic tools test **every component** in the streaming pipeline:

```
Camera → FFmpeg → RtspConnector → FrameBus → PreviewPublisher → HTTP API → Dashboard
   ✅       ✅         ✅            ✅           ✅             ✅        ❌
```

### System Level
- ✅ API server health
- ✅ Redis connectivity  
- ✅ Camera configuration
- ✅ JavaScript/frontend integration

### Camera Level
- ✅ Network connectivity
- ✅ RTSP authentication
- ✅ FFmpeg compatibility
- ✅ Stream format validation

### Pipeline Level  
- ✅ RtspConnector state machine
- ✅ FrameBus frame buffering
- ✅ PreviewPublisher MJPEG generation
- ✅ HTTP streaming endpoints

### Integration Level
- ✅ API endpoint responses
- ✅ Dashboard HTML/JavaScript
- ✅ Browser MJPEG rendering

## 🛠️ Manual Debugging Commands

If automatic tools don't solve your issue:

```bash
# Check specific camera status
curl http://localhost:8000/api/cameras/1/stats

# Enable streaming manually
curl -X POST http://localhost:8000/api/cameras/1/show

# Test MJPEG endpoint directly
curl http://localhost:8000/api/cameras/1/mjpeg

# Check application logs
tail -f logs/app.log | grep -i stream

# Test camera with external tool
ffmpeg -rtsp_transport tcp -i "rtsp://camera/stream" -frames:v 1 test.jpg
```

## 📁 Files Created

```
📁 Your Project Root/
├── 🔍 run_diagnostics.sh          # Main diagnostic runner
├── 🐍 diagnose_streaming.py       # Core diagnostic engine
├── 🔧 fix_streaming.py            # Automated fix tool
├── 📖 DIAGNOSTIC_GUIDE.md         # Comprehensive troubleshooting guide
└── 📋 STREAMING_TOOLS_README.md   # This file
```

## 🎉 Success Metrics

After running the tools, you should see:
- **Dashboard shows live video feeds** ✅
- **No "Stream unavailable" messages** ✅  
- **Diagnostic reports 90%+ success rate** ✅
- **MJPEG endpoints respond correctly** ✅

## 💡 Pro Tips

1. **Always run diagnostics first** - saves time by identifying the exact issue
2. **Use interactive mode** - guides you through fixes step by step
3. **Check browser console** - JavaScript errors can prevent streaming
4. **Test with external tools** - VLC/FFmpeg help isolate camera issues
5. **Clear cache** - Browser cache can cause display issues (Ctrl+F5)

## 🆘 Getting Help

If these tools don't resolve your issue:

1. **Save diagnostic output**:
   ```bash
   ./run_diagnostics.sh --verbose > diagnosis.txt 2>&1
   ```

2. **Provide system info**:
   - Camera make/model
   - Network topology  
   - Error messages
   - Diagnostic report

3. **Check specific logs**:
   ```bash
   grep -E "(STREAM_ERROR|capture_error|RTSP)" logs/app.log
   ```

## 🎯 The Bottom Line

These tools solve the #1 most common issue with this crowd management system: **camera tests work but dashboard streaming doesn't**. 

By testing every component in the complex streaming pipeline and automatically applying fixes, you can resolve streaming issues in minutes instead of hours.

**Happy streaming!** 📹✨