#!/bin/bash

# Camera Streaming Diagnostic Tool Runner
# ======================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔍 Camera Streaming Diagnostic Tool${NC}"
echo "======================================"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found. Please install Python 3.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python 3 found${NC}"

# Check if virtual environment exists and activate it
if [ -d "venv" ]; then
    echo -e "${GREEN}✓ Virtual environment found, activating...${NC}"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo -e "${GREEN}✓ Virtual environment found, activating...${NC}"
    source .venv/bin/activate
else
    echo -e "${YELLOW}⚠️  No virtual environment found, using system Python${NC}"
fi

# Install additional dependencies if needed
echo -e "${BLUE}📦 Checking dependencies...${NC}"
python3 -c "import requests" 2>/dev/null || {
    echo -e "${YELLOW}⚠️  Installing requests...${NC}"
    pip install requests
}

# Check if application is running
echo -e "${BLUE}🌐 Checking application status...${NC}"
APP_URL="http://localhost:8000"
if curl -s "$APP_URL/api/v1/health" >/dev/null 2>&1; then
    echo -e "${GREEN}✓ Application is running at $APP_URL${NC}"
else
    echo -e "${YELLOW}⚠️  Application may not be running at $APP_URL${NC}"
    echo "   Starting diagnostic anyway..."
fi

# Parse command line arguments
CAMERA_ID=""
CAMERA_URL=""
VERBOSE=""
BASE_URL="http://localhost:8000"

while [[ $# -gt 0 ]]; do
    case $1 in
        --camera-id)
            CAMERA_ID="--camera-id $2"
            shift 2
            ;;
        --url)
            CAMERA_URL="--url $2"
            shift 2
            ;;
        --base-url)
            BASE_URL="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE="--verbose"
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --camera-id ID    Test specific camera ID"
            echo "  --url URL         Test specific camera URL"
            echo "  --base-url URL    Application base URL (default: http://localhost:8000)"
            echo "  -v, --verbose     Verbose output"
            echo "  -h, --help        Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                           # Test all cameras"
            echo "  $0 --camera-id 1            # Test camera ID 1"
            echo "  $0 --url rtsp://cam/stream   # Test specific URL"
            echo "  $0 --verbose                 # Verbose output"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run the diagnostic
echo -e "${BLUE}🚀 Running diagnostic...${NC}"
echo ""

python3 diagnose_streaming.py --base-url "$BASE_URL" $CAMERA_ID $CAMERA_URL $VERBOSE

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Diagnostic completed successfully${NC}"
else
    echo ""
    echo -e "${RED}❌ Diagnostic completed with errors${NC}"
    echo -e "${YELLOW}💡 Check the recommendations above for fixes${NC}"
fi

echo ""
echo -e "${BLUE}📚 Additional Help:${NC}"
echo "  • Check logs: tail -f logs/app.log"
echo "  • Visit debug page: $BASE_URL/debug"
echo "  • API docs: $BASE_URL/docs"
echo "  • Enable camera streaming: curl -X POST $BASE_URL/api/cameras/{ID}/show"
