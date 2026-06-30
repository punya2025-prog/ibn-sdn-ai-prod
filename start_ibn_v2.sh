#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════
# IBN-SDN-AI Version 2 — Complete Startup Script
# Usage:  bash start_ibn_v2.sh [start|stop|status|restart|logs]
# ════════════════════════════════════════════════════════════════════════

set -euo pipefail
IBN_DIR="$HOME/ibn-sdn-ai-v2"
LOG_DIR="$IBN_DIR/logs"
PID_DIR="$IBN_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BLUE='\033[0;34m';  CYAN='\033[0;36m';  NC='\033[0m'
OK="${GREEN}[OK]${NC}"; FAIL="${RED}[FAIL]${NC}"; INFO="${BLUE}[INFO]${NC}"

log()  { echo -e "${CYAN}$(date '+%H:%M:%S')${NC}  $*"; }
ok()   { echo -e "${OK}  $*"; }
fail() { echo -e "${FAIL}  $*"; }
info() { echo -e "${INFO}  $*"; }

banner() {
echo -e "${BLUE}"
cat << 'BANNER'
 ╔══════════════════════════════════════════════════════════════════╗
 ║   Intent-Driven Business Network Modelling Using SDN and AI     ║
 ║   IBN-SDN-AI Version 2  —  RAG-Enabled Research System         ║
 ║   Port: 8001  Console: 9091  cEOS: 10.201.0.0/24               ║
 ╚══════════════════════════════════════════════════════════════════╝
BANNER
echo -e "${NC}"
}

# ════════════════════════════════════════════════════════════════════════
cmd_start() {
banner
cd "$IBN_DIR"

# ── 1. Load environment ─────────────────────────────────────────────
log "Loading environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    info "Created .env from template — review passwords before production use"
fi
export $(grep -v '^#' .env | grep -v '^$' | xargs)
ok "Environment loaded  (PORT=$PORT, RAG=$RAG_ENABLED)"

# ── 2. Python venv ──────────────────────────────────────────────────
log "Activating Python environment..."
if [ ! -d venv ]; then
    info "Creating venv..."
    python3.12 -m venv venv
fi
source venv/bin/activate
ok "Python $(python --version) — venv active"

# ── 3. Check OLLAMA ─────────────────────────────────────────────────
log "Checking OLLAMA..."
if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    MODEL=$(curl -sf http://localhost:11434/api/tags | python3 -c \
        "import sys,json; tags=json.load(sys.stdin).get('models',[]); \
         print(tags[0]['name'] if tags else 'none')" 2>/dev/null || echo "unknown")
    ok "OLLAMA running — model: $MODEL"
else
    fail "OLLAMA not running — starting..."
    ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
    sleep 3
    if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        ok "OLLAMA started"
    else
        info "OLLAMA may still be starting — continuing without LLM guarantee"
    fi
fi

# ── 4. Check Batfish ─────────────────────────────────────────────────
log "Checking Batfish..."
if curl -sf http://localhost:9996 > /dev/null 2>&1; then
    ok "Batfish container running"
else
    info "Starting Batfish container..."
    docker run -d --name batfish \
        -p 9996-9997:9996-9997 batfish/allinone \
        > /dev/null 2>&1 || true
    ok "Batfish started"
fi

# ── 5. ContainerLab digital twin ────────────────────────────────────
log "Checking ContainerLab digital twin..."
CLAB_STATUS=$(sudo containerlab inspect --all 2>/dev/null | \
    grep "ibn-digital-twin-ext" | wc -l || echo 0)

if [ "$CLAB_STATUS" -eq 0 ]; then
    info "Deploying ContainerLab topology..."
    sudo containerlab deploy \
        --topo topology/ibn-digital-twin-extended.clab.yml \
        > "$LOG_DIR/clab-deploy.log" 2>&1
    ok "ContainerLab deployed"
else
    ok "ContainerLab already running ($CLAB_STATUS containers)"
fi

# ── 6. Bridge management IP ─────────────────────────────────────────
log "Setting up management bridge..."
CLAB_BR=$(brctl show 2>/dev/null | \
    awk 'NR>1 && $1~/^br-/{print $1}' | tail -1 || echo "")
if [ -n "$CLAB_BR" ]; then
    sudo ip addr add 10.201.0.1/24 dev "$CLAB_BR" 2>/dev/null || true
    ok "Bridge $CLAB_BR → 10.201.0.1/24"
else
    info "No bridge found — cEOS may use direct networking"
fi

# ── 7. Enable eAPI on all cEOS switches ─────────────────────────────
log "Enabling eAPI on cEOS switches..."
declare -A NODES=(
    [dc-spine1]=10.201.0.11  [dc-spine2]=10.201.0.12
    [dc-leaf1]=10.201.0.21   [dc-leaf2]=10.201.0.22
    [dc-leaf3]=10.201.0.23   [dc-leaf4]=10.201.0.24
    [campus-core1]=10.201.0.31
    [campus-dist1]=10.201.0.32 [campus-dist2]=10.201.0.33
    [campus-access1]=10.201.0.41 [campus-access2]=10.201.0.42
    [campus-access3]=10.201.0.43 [campus-access4]=10.201.0.44
)
CEOS_OK=0
for NODE in "${!NODES[@]}"; do
    IP="${NODES[$NODE]}"
    CONTAINER="clab-ibn-digital-twin-ext-${NODE}"
    docker exec "$CONTAINER" Cli -c \
"enable
configure
management api http-commands
   protocol http
   no shutdown
!
management ssh
   no shutdown
!
end" > /dev/null 2>&1 && CEOS_OK=$((CEOS_OK+1)) || true
done
ok "eAPI enabled on $CEOS_OK/13 cEOS switches"

# ── 8. Verify eAPI reachability ─────────────────────────────────────
log "Verifying eAPI reachability..."
REACHABLE=0
for IP in 10.201.0.11 10.201.0.21 10.201.0.41; do
    if curl -sf -u admin:admin "http://$IP/command-api" \
       -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"]},"id":1}' \
       > /dev/null 2>&1; then
        REACHABLE=$((REACHABLE+1))
    fi
done
ok "$REACHABLE/3 spot-check switches reachable via eAPI"

# ── 9. RAG index check ──────────────────────────────────────────────
log "Checking RAG knowledge base..."
if [ -d rag_db ] && [ "$(ls -A rag_db 2>/dev/null)" ]; then
    ok "ChromaDB exists — $(du -sh rag_db | cut -f1)"
    TOTAL=$(python3 -c "
import chromadb
db=chromadb.PersistentClient(path='rag_db')
colls=['kb1_intents','kb2_topology','kb3_arp','kb4_mac','kb5_vlans','kb6_ports','kb7_policies']
total=sum(db.get_collection(c).count() for c in colls if c in [x.name for x in db.list_collections()])
print(total)" 2>/dev/null || echo "?")
    ok "RAG collections: $TOTAL total documents"
else
    info "RAG not indexed — building..."
    python3 netbox_seed.py --mode offline --output network_state 2>/dev/null || true
    python3 rag_indexer.py --rebuild 2>/dev/null || \
        info "RAG indexer not found — run manually: python3 rag_indexer.py"
fi

# ── 10. Start IBN gateway ────────────────────────────────────────────
log "Starting IBN gateway on port $PORT..."
if [ -f "$PID_DIR/gateway.pid" ]; then
    OLD_PID=$(cat "$PID_DIR/gateway.pid")
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
fi
nohup python3 main.py \
    > "$LOG_DIR/gateway.log" 2>&1 &
GATEWAY_PID=$!
echo $GATEWAY_PID > "$PID_DIR/gateway.pid"
sleep 3
if kill -0 $GATEWAY_PID 2>/dev/null; then
    ok "IBN gateway PID=$GATEWAY_PID — port $PORT"
else
    fail "Gateway failed to start — check $LOG_DIR/gateway.log"
    tail -20 "$LOG_DIR/gateway.log"
    exit 1
fi

# ── 11. Start console HTTP server ────────────────────────────────────
log "Starting IBN console on port 9091..."
if [ -f "$PID_DIR/console.pid" ]; then
    OLD_PID=$(cat "$PID_DIR/console.pid")
    kill "$OLD_PID" 2>/dev/null || true
fi
nohup python3 -m http.server 9091 \
    > "$LOG_DIR/console.log" 2>&1 &
CONSOLE_PID=$!
echo $CONSOLE_PID > "$PID_DIR/console.pid"
sleep 1
ok "Console PID=$CONSOLE_PID — port 9091"

# ── 12. Health check ─────────────────────────────────────────────────
log "Running health check..."
sleep 3
HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null || echo '{}')
STATUS=$(echo "$HEALTH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" \
    2>/dev/null || echo "no-response")

if [ "$STATUS" = "healthy" ]; then
    ok "Health check: $STATUS"
else
    fail "Health check: $STATUS — check logs"
fi

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  IBN-SDN-AI V2 is running                               ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "  Gateway:   ${CYAN}http://$(hostname -I | awk '{print $1}'):$PORT${NC}"
echo -e "  Console:   ${CYAN}http://$(hostname -I | awk '{print $1}'):9091/ibn_console.html${NC}"
echo -e "  API docs:  ${CYAN}http://$(hostname -I | awk '{print $1}'):$PORT/docs${NC}"
echo -e "  Health:    ${CYAN}http://$(hostname -I | awk '{print $1}'):$PORT/health${NC}"
echo -e "  Logs:      ${CYAN}tail -f $LOG_DIR/gateway.log${NC}"
echo ""
echo -e "  Test intent:"
echo -e "  ${YELLOW}curl -s -X POST http://localhost:$PORT/api/channel/0/prompt${NC}"
echo -e "  ${YELLOW}  -H 'Content-Type: application/json'${NC}"
echo -e "  ${YELLOW}  -d '{\"text\":\"block IoT VLAN 300 from corporate\",\"simulate\":true}'${NC}"
echo -e "  ${YELLOW}  | python3 -m json.tool${NC}"
echo ""
}

# ════════════════════════════════════════════════════════════════════════
cmd_stop() {
log "Stopping IBN-SDN-AI V2..."
for svc in gateway console; do
    PF="$PID_DIR/$svc.pid"
    if [ -f "$PF" ]; then
        PID=$(cat "$PF")
        kill "$PID" 2>/dev/null && ok "Stopped $svc (PID=$PID)" || \
            info "$svc was not running"
        rm -f "$PF"
    fi
done
info "ContainerLab and Batfish kept running (use --all to stop those too)"
}

cmd_stop_all() {
cmd_stop
log "Stopping ContainerLab..."
cd "$IBN_DIR"
sudo containerlab destroy --topo topology/ibn-digital-twin-extended.clab.yml 2>/dev/null && \
    ok "ContainerLab stopped" || info "ContainerLab was not running"
docker stop batfish 2>/dev/null && ok "Batfish stopped" || true
}

# ════════════════════════════════════════════════════════════════════════
cmd_status() {
echo ""
echo -e "${BLUE}══ IBN-SDN-AI V2 Status ══${NC}"
cd "$IBN_DIR"
export $(grep -v '^#' .env 2>/dev/null | grep -v '^$' | xargs 2>/dev/null || true)
PORT=${PORT:-8001}

# Gateway
if [ -f "$PID_DIR/gateway.pid" ] && kill -0 $(cat "$PID_DIR/gateway.pid") 2>/dev/null; then
    PID=$(cat "$PID_DIR/gateway.pid")
    HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null | \
        python3 -c "import sys,json; d=json.load(sys.stdin); \
        print(f\"healthy  intents={d.get('intents_stored',0)}  \
rag={'on' if d.get('rag_enabled') else 'off'}\")" 2>/dev/null || echo "unreachable")
    ok "Gateway PID=$PID  $HEALTH"
else
    fail "Gateway: not running"
fi

# Console
if [ -f "$PID_DIR/console.pid" ] && kill -0 $(cat "$PID_DIR/console.pid") 2>/dev/null; then
    ok "Console: running (port 9091)"
else
    fail "Console: not running"
fi

# OLLAMA
if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    ok "OLLAMA: running"
else fail "OLLAMA: not running"; fi

# Batfish
if curl -sf http://localhost:9996 > /dev/null 2>&1; then
    ok "Batfish: running"
else fail "Batfish: not running"; fi

# ContainerLab
CLAB=$(sudo containerlab inspect --all 2>/dev/null | \
    grep -c "ibn-digital-twin-ext" || echo 0)
if [ "$CLAB" -gt 0 ]; then
    ok "ContainerLab: $CLAB cEOS containers running"
else fail "ContainerLab: not running"; fi

echo ""
}

# ════════════════════════════════════════════════════════════════════════
cmd_logs() {
cd "$IBN_DIR"
echo -e "${BLUE}══ Gateway log (Ctrl+C to exit) ══${NC}"
tail -f "$LOG_DIR/gateway.log"
}

cmd_test() {
cd "$IBN_DIR"
export $(grep -v '^#' .env | grep -v '^$' | xargs)
PORT=${PORT:-8001}
log "Running quick tests..."

# Test 1: Health
HEALTH=$(curl -sf "http://localhost:$PORT/health" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))" 2>/dev/null)
[ "$HEALTH" = "healthy" ] && ok "Health check" || fail "Health check: $HEALTH"

# Test 2: Intent
RESULT=$(curl -sf -X POST "http://localhost:$PORT/api/channel/0/prompt" \
    -H "Content-Type: application/json" \
    -d '{"text":"block IoT VLAN 300 from corporate network","simulate":true}' | \
    python3 -c "
import sys,json
d=json.load(sys.stdin)
score=d.get('score',0)
verdict=d.get('simulation',{}).get('verdict','?')
print(f'score={score:.3f} verdict={verdict}')
" 2>/dev/null || echo "failed")
ok "Intent test: $RESULT"
}

# ════════════════════════════════════════════════════════════════════════
# Main dispatcher
COMMAND="${1:-start}"
case "$COMMAND" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    stopall) cmd_stop_all;;
    status)  cmd_status  ;;
    restart) cmd_stop; sleep 2; cmd_start ;;
    logs)    cmd_logs    ;;
    test)    cmd_test    ;;
    *)
        echo "Usage: $0 {start|stop|stopall|restart|status|logs|test}"
        exit 1
        ;;
esac
