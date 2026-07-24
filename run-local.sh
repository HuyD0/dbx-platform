#!/bin/bash
set -e

# Local development script for dbx-platform
# Starts both backend and frontend with proper configuration

# Configuration
BACKEND_DIR="apps/platform-console"
FRONTEND_DIR="apps/platform-console/frontend"
WAREHOUSE_ID="83611e41c0041b91"  # [dbx-platform] mission-control

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Starting dbx-platform local development environment...${NC}\n"

# Check if we're in the right directory
if [ ! -f "databricks.yml" ]; then
  echo "Error: databricks.yml not found. Please run this script from the repo root."
  exit 1
fi

# Export environment variables for local mode
export DBX_PLATFORM_LOCAL_IDENTITY=true
export DBX_PLATFORM_LOCAL_ACTOR_ID="${DATABRICKS_HOST_USER:-huy.d@hotmail.com}"
export DBX_PLATFORM_LOCAL_ROLES=approver
export DBX_PLATFORM_WAREHOUSE_ID="$WAREHOUSE_ID"

# Use azure-cli auth (works better in sandboxed environments; databricks-cli TLS can fail)
export DATABRICKS_HOST=https://adb-7405609799238491.11.azuredatabricks.net
export DATABRICKS_AUTH_TYPE=azure-cli

# If DATABRICKS_CONFIG_PROFILE is set locally, use it instead (takes precedence)
if [ -z "$DATABRICKS_CONFIG_PROFILE" ] && command -v az &> /dev/null; then
  echo -e "${YELLOW}Using azure-cli authentication${NC}"
else
  echo -e "${YELLOW}Using databricks-cli profile${NC}"
fi

echo ""
echo -e "${GREEN}Backend environment:${NC}"
echo "  DBX_PLATFORM_LOCAL_IDENTITY=$DBX_PLATFORM_LOCAL_IDENTITY"
echo "  DBX_PLATFORM_LOCAL_ACTOR_ID=$DBX_PLATFORM_LOCAL_ACTOR_ID"
echo "  DBX_PLATFORM_LOCAL_ROLES=$DBX_PLATFORM_LOCAL_ROLES"
echo "  DBX_PLATFORM_WAREHOUSE_ID=$DBX_PLATFORM_WAREHOUSE_ID"
echo ""

# Check for required commands
if ! command -v python3 &> /dev/null; then
  echo "Error: python3 not found. Please install Python 3."
  exit 1
fi

if ! command -v npm &> /dev/null; then
  echo "Error: npm not found. Please install Node.js and npm."
  exit 1
fi

# Install Python dependencies if needed
echo -e "${BLUE}Checking Python dependencies...${NC}"
PYTHON_BIN=$(which python3)
echo "Using Python: $(python3 --version) at $PYTHON_BIN"
if ! python3 -c "import uvicorn, fastapi" 2>/dev/null; then
  echo "Installing Python dependencies..."
  python3 -m pip install -e ".[dev]" 2>&1 | grep -E "Successfully|already" || true
else
  echo "  uvicorn and fastapi already installed"
fi
echo -e "${GREEN}✓ Python dependencies ready${NC}\n"

# Function to cleanup on exit
cleanup() {
  echo -e "\n${YELLOW}Cleaning up processes...${NC}"
  jobs -p | xargs -r kill 2>/dev/null || true
}

trap cleanup EXIT

# Start backend in background
echo -e "${BLUE}Starting backend (FastAPI server)...${NC}"
cd "$BACKEND_DIR"
# Use explicit python path captured before cd (avoid pyenv auto-switching)
"$PYTHON_BIN" main.py &
BACKEND_PID=$!
echo -e "${GREEN}Backend started (PID: $BACKEND_PID)${NC}"
echo "  URL: http://localhost:8000"
echo ""

# Give backend time to start
sleep 2

# Check if backend is running
if ! kill -0 $BACKEND_PID 2>/dev/null; then
  echo -e "${YELLOW}Warning: Backend process died. Check logs above.${NC}"
  exit 1
fi

# Start frontend in background
echo -e "${BLUE}Starting frontend (Vite dev server)...${NC}"
cd - > /dev/null  # Back to repo root
cd "$FRONTEND_DIR"
npm ci > /dev/null 2>&1 || true  # Silent install of deps if needed
npm run dev &
FRONTEND_PID=$!
echo -e "${GREEN}Frontend starting (PID: $FRONTEND_PID)${NC}"
echo ""

echo -e "${GREEN}✓ Both services started!${NC}"
echo ""
echo -e "${BLUE}Available URLs:${NC}"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: Check console output above for Vite dev server URL"
echo ""
echo -e "${YELLOW}Approvals/writes are in-memory and proposal-only (lost on restart).${NC}"
echo "Press Ctrl+C to stop both services."
echo ""

# Wait for both processes
wait
