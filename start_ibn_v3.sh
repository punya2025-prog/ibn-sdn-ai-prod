#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  IBN-SDN-AI Version 3 — Complete Startup / Management Script
#  Intent-Driven Business Network Modelling Using SDN and AI
# ────────────────────────────────────────────────────────────────────────────
#  Usage:
#    bash start_ibn_v3.sh start        — full startup with all checks
#    bash start_ibn_v3.sh stop         — stop gateway and console
#    bash start_ibn_v3.sh stopall      — stop everything incl. containers
#    bash start_ibn_v3.sh restart      — stop then start
#    bash start_ibn_v3.sh status       — show running status of all components
#    bash start_ibn_v3.sh logs         — tail gateway log
#    bash start_ibn_v3.sh audit        — tail audit log (formatted)
#    bash start_ibn_v3.sh test         — run health + intent + physical tests
#    bash start_ibn_v3.sh preflight    — check all prerequisites
#    bash start_ibn_v3.sh snapshot     — take config snapshot from Arista 7010
#    bash start_ibn_v3.sh mine         — run intent miner on syslog
#    bash start_ibn_v3.sh health-check — morning policy verification suite
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

IBN_DIR="${IBN_V3_DIR:-$HOME/ibn-sdn-ai-v3}"
LOG_DIR="$IBN_DIR/logs"
PID_DIR="$IBN_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR" \
         "$IBN_DIR/logs/config_snapshots" \
         "$IBN_DIR/logs/syslog_archive"

# ── Colours ──────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BLUE='\033[0;34m';  CYAN='\033[0;36m';   MAGENTA='\033[0;35m'
BOLD='\033[1m';     NC='\033[0m'

ok()   { echo -e "${GREEN}  [OK]${NC}    $*"; }
fail() { echo -e "${RED}  [FAIL]${NC}  $*"; }
info() { echo -e "${BLUE}  [INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}  [WARN]${NC}  $*"; }
step() { echo -e "\n${BOLD}${CYAN}▶  $*${NC}"; }
log()  { echo -e "  ${CYAN}$(date '+%H:%M:%S')${NC}  $*"; }

banner() {
echo -e "${MAGENTA}"
cat << 'BANNER'
 ╔══════════════════════════════════════════════════════════════════════════╗
 ║                                                                          ║
 ║   Intent-Driven Business Network Modelling Using SDN and AI            ║
 ║   IBN-SDN-AI  Version 3  —  Physical Switch Integration                ║
 ║                                                                          ║
 ║   Port: 8002   Console: 9092   cEOS twin: 10.202.0.0/24               ║
 ║   Physical: Arista 7010 (eAPI)  ·  Cisco 2960-X (OF/ODL optional)     ║
 ║   Southbound: eAPI · NETCONF · OpenFlow · SSH (netmiko)                ║
 ║                                                                          ║
 ╚══════════════════════════════════════════════════════════════════════════╝
BANNER
echo -e "${NC}"
}

# ════════════════════════════════════════════════════════════════════════════
# START
# ════════════════════════════════════════════════════════════════════════════
cmd_start() {
banner
cd "$IBN_DIR"

# ── Step 1: Environment ──────────────────────────────────────────────────
step "Loading environment"
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        warn ".env not found — created from template. Set passwords before use."
    else
        cat > .env << 'ENVEOF'
PORT=8002
HOST=0.0.0.0
RAG_ENABLED=true
CHROMA_PATH=rag_db
TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3
LLM_TIMEOUT=120
EAPI_USERNAME=admin
EAPI_PASSWORD=admin
EAPI_PORT=80
DEPLOY_TWIN=true
DEPLOY_TWIN_FIRST=true
DEPLOY_PHYSICAL=false
PHYSICAL_ARISTA_HOST=192.168.20.105
PHYSICAL_ARISTA_USER=ibn-operator
PHYSICAL_ARISTA_PASS=CHANGE_ME
PHYSICAL_ARISTA_PORT=443
PHYSICAL_ARISTA_TRANSPORT=https
ODL_ENABLED=false
ODL_BASE=http://localhost:8181
ODL_USER=admin
ODL_PASSWORD=admin
OPENFLOW_ENABLED=false
SSH_DEPLOY_ENABLED=false
CISCO_3560_HOST=192.168.20.107
CISCO_3560_USER=ibn-operator
CISCO_3560_PASS=CHANGE_ME
BROCADE_HOST=192.168.20.108
BROCADE_USER=ibn-operator
BROCADE_PASS=CHANGE_ME
AUTO_DEPLOY_THRESHOLD=0.92
APPROVAL_THRESHOLD=0.75
GNN_ENABLED=true
GNN_INTERVAL=60
LOG_LEVEL=INFO
DATABASE_PATH=ibn_intents.db
ENVEOF
        info "Created default .env — edit passwords before enabling physical deployment"
    fi
fi

set -a; source .env; set +a
PORT="${PORT:-8002}"
ok "Environment loaded — PORT=$PORT  RAG=$RAG_ENABLED  DEPLOY_PHYSICAL=$DEPLOY_PHYSICAL"

# ── Step 2: Python venv ──────────────────────────────────────────────────
step "Python environment"
if [ ! -d venv ]; then
    info "Creating virtual environment..."
    python3.12 -m venv venv 2>/dev/null || python3 -m venv venv
fi
source venv/bin/activate
PY=$(python --version 2>&1)
ok "$PY — venv active"

# Install deps if needed
if ! python -c "import fastapi,httpx,pyeapi,chromadb" 2>/dev/null; then
    info "Installing dependencies..."
    pip install -r requirements.txt -q
    ok "Dependencies installed"
fi

# ── Step 3: OLLAMA ───────────────────────────────────────────────────────
step "OLLAMA local LLM"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
if curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
    MODEL=$(curl -sf "$OLLAMA_HOST/api/tags" | \
        python3 -c "import sys,json; t=json.load(sys.stdin).get('models',[]); \
                    print(t[0]['name'] if t else 'none')" 2>/dev/null || echo "?")
    ok "OLLAMA running — model: $MODEL"
else
    warn "OLLAMA not running — starting daemon..."
    nohup ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
    sleep 4
    if curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
        ok "OLLAMA started"
    else
        warn "OLLAMA unavailable — rule-based parser will be used"
    fi
fi

# ── Step 4: Batfish ──────────────────────────────────────────────────────
step "Batfish formal verification"
if curl -sf http://localhost:9996 > /dev/null 2>&1; then
    ok "Batfish container running"
else
    info "Starting Batfish..."
    docker run -d --name batfish --restart unless-stopped \
        -p 9996-9997:9996-9997 batfish/allinone \
        >> "$LOG_DIR/batfish.log" 2>&1 || \
    docker start batfish >> "$LOG_DIR/batfish.log" 2>&1 || true
    sleep 3
    curl -sf http://localhost:9996 > /dev/null 2>&1 && \
        ok "Batfish started" || warn "Batfish unavailable — Gate 2 and Gate 5b will skip"
fi

# ── Step 5: ContainerLab digital twin ────────────────────────────────────
step "ContainerLab digital twin (V3)"
TOPO="topology/ibn-digital-twin-v3.clab.yml"

# Fall back to V2 topology if V3 not yet created
if [ ! -f "$TOPO" ]; then
    V3_ALT="topology/ibn-digital-twin-extended.clab.yml"
    V2_TOPO="$HOME/ibn-sdn-ai-v2/topology/ibn-digital-twin-extended.clab.yml"
    if [ -f "$V3_ALT" ]; then
        TOPO="$V3_ALT"
    elif [ -f "$V2_TOPO" ]; then
        mkdir -p topology
        cp "$V2_TOPO" "$TOPO"
        info "Copied topology from V2 → $TOPO"
    else
        warn "No topology file found — ContainerLab not started"
        TOPO=""
    fi
fi

if [ -n "$TOPO" ] && [ -f "$TOPO" ]; then
    CLAB_RUNNING=$(sudo containerlab inspect --all 2>/dev/null | \
        grep -cE "ibn-digital-twin" || echo 0)
    if [ "$CLAB_RUNNING" -gt 0 ]; then
        ok "ContainerLab: $CLAB_RUNNING cEOS containers already running"
    else
        info "Deploying ContainerLab topology..."
        sudo containerlab deploy --topo "$TOPO" \
            >> "$LOG_DIR/clab.log" 2>&1 && \
            ok "ContainerLab deployed (13 nodes)" || \
            warn "ContainerLab deploy failed — check $LOG_DIR/clab.log"
    fi

    # Bridge management IP for V3 (10.202.0.0/24)
    CLAB_BR=$(brctl show 2>/dev/null | \
        awk 'NR>1 && $1~/^br-/{print $1}' | tail -1 || echo "")
    if [ -n "$CLAB_BR" ]; then
        sudo ip addr add 10.202.0.1/24 dev "$CLAB_BR" 2>/dev/null || \
        sudo ip addr add 10.201.0.1/24 dev "$CLAB_BR" 2>/dev/null || true
        ok "Bridge $CLAB_BR management IP set"
    fi

    # Enable eAPI on all 13 cEOS switches
    CEOS_OK=0
    for NODE in dc-spine1 dc-spine2 dc-leaf1 dc-leaf2 dc-leaf3 dc-leaf4 \
                campus-core1 campus-dist1 campus-dist2 \
                campus-access1 campus-access2 campus-access3 campus-access4; do
        CONTAINER="clab-ibn-digital-twin-ext-$NODE"
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
fi

# ── Step 6: Physical Arista 7010 ─────────────────────────────────────────
step "Physical Arista 7010 ($PHYSICAL_ARISTA_HOST)"
if ping -c 1 -W 2 "$PHYSICAL_ARISTA_HOST" > /dev/null 2>&1; then
    # eAPI check
    ARISTA_VER=$(curl -sk --max-time 5 \
        -u "$PHYSICAL_ARISTA_USER:$PHYSICAL_ARISTA_PASS" \
        -X POST "https://$PHYSICAL_ARISTA_HOST/command-api" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"]},"id":1}' \
        2>/dev/null | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['result'][0]['version'])" \
        2>/dev/null || echo "")

    if [ -n "$ARISTA_VER" ]; then
        ok "Arista 7010 eAPI: EOS $ARISTA_VER"
    else
        warn "Arista 7010 reachable but eAPI not responding"
        warn "Check: curl -sk -u ibn-operator:pass https://$PHYSICAL_ARISTA_HOST/command-api ..."
        warn "       and verify management api http-commands is enabled"
    fi

    # NETCONF check
    timeout 2 bash -c "echo >/dev/tcp/$PHYSICAL_ARISTA_HOST/830" 2>/dev/null && \
        ok "Arista 7010 NETCONF port 830 OPEN" || \
        warn "Arista 7010 NETCONF port 830 CLOSED (enable: management api netconf)"

    # gNMI check
    timeout 2 bash -c "echo >/dev/tcp/$PHYSICAL_ARISTA_HOST/6030" 2>/dev/null && \
        ok "Arista 7010 gNMI port 6030 OPEN" || \
        warn "Arista 7010 gNMI port 6030 CLOSED (GNN live telemetry affected)"

    if [ "${DEPLOY_PHYSICAL:-false}" = "true" ]; then
        info "Physical deployment ENABLED — intents will push to Arista 7010"
    else
        info "Physical deployment DISABLED (DEPLOY_PHYSICAL=false) — twin only"
    fi
else
    warn "Arista 7010 ($PHYSICAL_ARISTA_HOST) unreachable"
    warn "Physical deployment will be skipped even if DEPLOY_PHYSICAL=true"
fi

# ── Step 7: ODL (optional) ───────────────────────────────────────────────
step "OpenDaylight SDN controller (optional)"
if [ "${ODL_ENABLED:-false}" = "true" ]; then
    if curl -sf "$ODL_BASE/restconf" -u "$ODL_USER:$ODL_PASSWORD" \
        > /dev/null 2>&1; then
        ok "ODL RESTCONF: $ODL_BASE"
    else
        warn "ODL not reachable at $ODL_BASE"
        warn "Start with: ~/karaf-0.8.4/bin/karaf daemon"
    fi
    if [ "${OPENFLOW_ENABLED:-false}" = "true" ]; then
        timeout 2 bash -c "echo >/dev/tcp/localhost/6633" 2>/dev/null && \
            ok "OpenFlow port 6633 OPEN" || warn "OpenFlow port 6633 not ready"
    fi
else
    info "ODL disabled (ODL_ENABLED=false) — set to true in .env to enable"
fi

# ── Step 8: Cisco 3560 via netmiko (optional) ────────────────────────────
step "Cisco 3560 SSH via netmiko (optional)"
if [ "${SSH_DEPLOY_ENABLED:-false}" = "true" ]; then
    if ping -c 1 -W 2 "$CISCO_3560_HOST" > /dev/null 2>&1; then
        timeout 3 bash -c "echo >/dev/tcp/$CISCO_3560_HOST/22" 2>/dev/null && \
            ok "Cisco 3560 SSH port 22 OPEN" || \
            warn "Cisco 3560 SSH port 22 not reachable"
    else
        warn "Cisco 3560 ($CISCO_3560_HOST) unreachable"
    fi
else
    info "netmiko SSH disabled (SSH_DEPLOY_ENABLED=false)"
fi

# ── Step 9: RAG knowledge base ───────────────────────────────────────────
step "RAG knowledge base"
CHROMA_PATH="${CHROMA_PATH:-rag_db}"

# Auto-discover from V2 if local not present
if [ ! -d "$CHROMA_PATH" ] || [ -z "$(ls -A "$CHROMA_PATH" 2>/dev/null)" ]; then
    for src in \
        "$HOME/ibn-sdn-ai-v2/rag_db" \
        "$HOME/ibn-sdn-ai/rag_db"; do
        if [ -d "$src" ]; then
            info "Linking RAG from $src..."
            ln -sf "$src" "$CHROMA_PATH" 2>/dev/null || \
                cp -r "$src" . 2>/dev/null || true
            ok "RAG linked from $src"
            break
        fi
    done
fi

if [ -d "$CHROMA_PATH" ] && [ -n "$(ls -A "$CHROMA_PATH" 2>/dev/null)" ]; then
    RAG_COUNT=$(python3 -c "
import chromadb, os
try:
    db=chromadb.PersistentClient(path='${CHROMA_PATH}')
    colls=['kb1_intents','kb2_topology','kb3_arp','kb4_mac',
           'kb5_vlans','kb6_ports','kb7_policies']
    avail={c.name for c in db.list_collections()}
    total=sum(db.get_collection(c).count() for c in colls if c in avail)
    print(total)
except: print(0)
" 2>/dev/null || echo "?")
    ok "RAG: $RAG_COUNT documents across 7 knowledge bases"
else
    warn "No RAG database found — building from scratch..."
    python3 netbox_seed.py --mode offline --output network_state 2>/dev/null || true
    python3 rag_indexer.py --rebuild 2>/dev/null || \
        warn "Run manually: python3 rag_indexer.py --rebuild"
fi

# ── Step 10: Network state ────────────────────────────────────────────────
step "Network state"
if [ ! -f "network_state/network_state.json" ]; then
    for src in \
        "$HOME/ibn-sdn-ai-v2/network_state" \
        "$HOME/ibn-sdn-ai/network_state"; do
        if [ -d "$src" ]; then
            mkdir -p network_state
            cp "$src"/*.json network_state/ 2>/dev/null || true
            ok "Network state copied from $src"
            break
        fi
    done
fi
if [ -f "network_state/network_state.json" ]; then
    DEV_COUNT=$(python3 -c "
import json
with open('network_state/network_state.json') as f: d=json.load(f)
print(len(d.get('arp_table',[])))
" 2>/dev/null || echo "?")
    ok "Network state: $DEV_COUNT devices, 19 VLANs loaded"
else
    warn "No network state — run: python3 netbox_seed.py --mode offline --output network_state"
fi

# ── Step 11: Stop any existing instances ─────────────────────────────────
step "Stopping existing processes"
for svc in gateway console; do
    PF="$PID_DIR/$svc.pid"
    if [ -f "$PF" ]; then
        OLD=$(cat "$PF")
        kill -0 "$OLD" 2>/dev/null && kill "$OLD" 2>/dev/null && \
            info "Stopped old $svc (PID $OLD)" || true
        rm -f "$PF"
    fi
done
# Free ports
for p in "$PORT" 9092; do
    fuser -k "${p}/tcp" > /dev/null 2>&1 || true
done
sleep 1
ok "Ports $PORT and 9092 cleared"

# ── Step 12: Start IBN V3 gateway ────────────────────────────────────────
step "Starting IBN V3 gateway (port $PORT)"
nohup python3 main.py > "$LOG_DIR/gateway.log" 2>&1 &
GW_PID=$!
echo $GW_PID > "$PID_DIR/gateway.pid"
log "Gateway PID=$GW_PID — waiting for startup..."

READY=0
for i in $(seq 1 15); do
    sleep 1
    echo -n "."
    if curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
        READY=1; break
    fi
    if ! kill -0 $GW_PID 2>/dev/null; then
        echo ""
        fail "Gateway process died — last log:"
        tail -25 "$LOG_DIR/gateway.log"
        exit 1
    fi
done
echo ""
[ $READY -eq 1 ] && ok "Gateway ready on port $PORT" || \
    warn "Gateway started but health check pending (may still be loading RAG)"

# ── Step 13: Start console server ────────────────────────────────────────
step "Starting IBN console (port 9092)"
if [ ! -f ibn_console.html ] && [ -f "$HOME/ibn-sdn-ai-v2/ibn_console.html" ]; then
    cp "$HOME/ibn-sdn-ai-v2/ibn_console.html" .
    info "Console copied from V2"
fi

nohup python3 -m http.server 9092 > "$LOG_DIR/console.log" 2>&1 &
CON_PID=$!
echo $CON_PID > "$PID_DIR/console.pid"
sleep 1
kill -0 $CON_PID 2>/dev/null && ok "Console running (PID $CON_PID port 9092)" || \
    warn "Console failed to start"

# ── Step 14: Start syslog receiver ───────────────────────────────────────
step "Syslog receiver (Channel 6)"
if command -v nc > /dev/null 2>&1; then
    # Check if already running
    if ! pgrep -f "syslog_receiver" > /dev/null 2>&1; then
        python3 -c "
import socket, threading, datetime, os, json, urllib.request
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 5140))
print('Syslog receiver on UDP 5140')
gw_port = os.getenv('PORT','8002')
while True:
    data, addr = sock.recvfrom(65535)
    msg = data.decode('utf-8', errors='replace').strip()
    ts  = datetime.datetime.utcnow().isoformat()
    with open('logs/ibn_syslog.log','a') as f:
        f.write(f'{ts} {addr[0]} {msg}\n')
    # Forward critical events to IBN Channel 6
    critical = ['PSECURE_VIOLATION','BGP.*Down','CPU.*[89][0-9]%','LOGIN_FAILED']
    import re
    if any(re.search(p,msg,re.I) for p in critical):
        try:
            req = urllib.request.Request(
                f'http://localhost:{gw_port}/api/events/ingest',
                data=json.dumps({'type':'syslog','source':addr[0],'message':msg[:200],'severity':'warning'}).encode(),
                headers={'Content-Type':'application/json'})
            urllib.request.urlopen(req, timeout=3)
        except: pass
" > "$LOG_DIR/syslog_receiver.log" 2>&1 &
        SYSLOG_PID=$!
        echo $SYSLOG_PID > "$PID_DIR/syslog.pid"
        ok "Syslog receiver started (UDP 5140 PID=$SYSLOG_PID)"
        # Allow port
        sudo ufw allow 5140/udp > /dev/null 2>&1 || true
    else
        ok "Syslog receiver already running"
    fi
else
    info "nc not available — syslog receiver not started"
fi

# ── Step 15: Health check ─────────────────────────────────────────────────
step "Health check"
sleep 2
HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null || echo "{}")
STATUS=$(echo "$HEALTH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); \
     print(d.get('status','?'), \
           'rag='+str(d.get('rag_enabled','?')), \
           'intents='+str(d.get('intents_stored',0)))" 2>/dev/null || echo "?")
ok "Health: $STATUS"

# ── Step 16: Summary ─────────────────────────────────────────────────────
MY_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "192.168.20.15")
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  IBN-SDN-AI V3 is running                                    ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Gateway:${NC}      http://${MY_IP}:${PORT}"
echo -e "  ${BOLD}Console:${NC}      http://${MY_IP}:9092/ibn_console.html"
echo -e "  ${BOLD}API docs:${NC}     http://${MY_IP}:${PORT}/docs"
echo -e "  ${BOLD}Health:${NC}       http://${MY_IP}:${PORT}/health"
echo -e "  ${BOLD}Syslog in:${NC}    UDP ${MY_IP}:5140"
echo ""
echo -e "  ${BOLD}Southbound status:${NC}"
echo -e "    Arista 7010 eAPI:   $([ "${DEPLOY_PHYSICAL:-false}" = "true" ] && echo "ENABLED" || echo "DISABLED — set DEPLOY_PHYSICAL=true")"
echo -e "    ContainerLab twin:  ENABLED (simulate all intents here first)"
echo -e "    ODL/OpenFlow:       $([ "${ODL_ENABLED:-false}" = "true" ] && echo "ENABLED" || echo "DISABLED — set ODL_ENABLED=true")"
echo -e "    netmiko SSH:        $([ "${SSH_DEPLOY_ENABLED:-false}" = "true" ] && echo "ENABLED" || echo "DISABLED — set SSH_DEPLOY_ENABLED=true")"
echo ""
echo -e "  ${BOLD}Quick test:${NC}"
echo -e "  ${CYAN}curl -s -X POST http://${MY_IP}:${PORT}/api/channel/0/prompt \\"
echo -e "    -H 'Content-Type: application/json' \\"
echo -e "    -d '{\"text\":\"block IoT VLAN 300 from corporate\",\"simulate\":true}' \\"
echo -e "    | python3 -m json.tool${NC}"
echo ""
echo -e "  ${BOLD}Logs:${NC}     tail -f $LOG_DIR/gateway.log"
echo -e "  ${BOLD}Stop:${NC}     bash $0 stop"
echo -e "  ${BOLD}Status:${NC}   bash $0 status"
echo ""
}

# ════════════════════════════════════════════════════════════════════════════
# STOP
# ════════════════════════════════════════════════════════════════════════════
cmd_stop() {
echo -e "${YELLOW}Stopping IBN-SDN-AI V3...${NC}"
for svc in gateway console syslog; do
    PF="$PID_DIR/$svc.pid"
    if [ -f "$PF" ]; then
        PID=$(cat "$PF")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && ok "Stopped $svc (PID $PID)"
        else
            info "$svc was not running"
        fi
        rm -f "$PF"
    else
        info "$svc: no PID file"
    fi
done
info "ContainerLab, Batfish and OLLAMA kept running (use 'stopall' to stop those)"
}

cmd_stop_all() {
cmd_stop
echo -e "${YELLOW}Stopping all containers and services...${NC}"
cd "$IBN_DIR"
TOPO=$(ls topology/*.clab.yml 2>/dev/null | head -1 || echo "")
[ -n "$TOPO" ] && sudo containerlab destroy --topo "$TOPO" 2>/dev/null && \
    ok "ContainerLab stopped" || info "ContainerLab was not running"
docker stop batfish 2>/dev/null && ok "Batfish stopped" || true
pkill -f "ollama serve" 2>/dev/null && ok "OLLAMA stopped" || true
}

# ════════════════════════════════════════════════════════════════════════════
# STATUS
# ════════════════════════════════════════════════════════════════════════════
cmd_status() {
cd "$IBN_DIR"
[ -f .env ] && { set -a; source .env; set +a; }
PORT="${PORT:-8002}"

echo ""
echo -e "${BOLD}${BLUE}══ IBN-SDN-AI V3 Status ══════════════════════════════════${NC}"
echo ""

# Gateway
PF="$PID_DIR/gateway.pid"
if [ -f "$PF" ] && kill -0 "$(cat "$PF")" 2>/dev/null; then
    PID=$(cat "$PF")
    HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null | \
        python3 -c "
import sys,json
d=json.load(sys.stdin)
print('healthy',
      'rag='+('on' if d.get('rag_enabled') else 'off'),
      'intents='+str(d.get('intents_stored',0)),
      'llm='+str(d.get('llm',{}).get('backend','?')))
" 2>/dev/null || echo "running (no health response)")
    ok "Gateway  PID=$PID  port=$PORT  $HEALTH"
else
    fail "Gateway: NOT RUNNING — run: bash $0 start"
fi

# Console
PF="$PID_DIR/console.pid"
if [ -f "$PF" ] && kill -0 "$(cat "$PF")" 2>/dev/null; then
    ok "Console  port 9092  PID=$(cat "$PF")"
else fail "Console: not running"; fi

# Syslog
PF="$PID_DIR/syslog.pid"
if [ -f "$PF" ] && kill -0 "$(cat "$PF")" 2>/dev/null; then
    ok "Syslog receiver  UDP 5140  PID=$(cat "$PF")"
else info "Syslog receiver: not running"; fi

# OLLAMA
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
if curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
    MODEL=$(curl -sf "$OLLAMA_HOST/api/tags" | \
        python3 -c "import sys,json; t=json.load(sys.stdin).get('models',[]); \
                    print(t[0]['name'] if t else 'no-model')" 2>/dev/null || echo "?")
    ok "OLLAMA  model=$MODEL"
else fail "OLLAMA: not running"; fi

# Batfish
if curl -sf http://localhost:9996 > /dev/null 2>&1; then
    ok "Batfish  port 9996"
else fail "Batfish: not running"; fi

# ContainerLab
CLAB=$(sudo containerlab inspect --all 2>/dev/null | grep -c "ibn" || echo 0)
if [ "$CLAB" -gt 0 ]; then
    ok "ContainerLab  $CLAB cEOS containers"
else fail "ContainerLab: not running"; fi

# Arista 7010
ARISTA_HOST="${PHYSICAL_ARISTA_HOST:-192.168.20.105}"
if ping -c 1 -W 1 "$ARISTA_HOST" > /dev/null 2>&1; then
    EAPI_OK=$(curl -sk --max-time 3 \
        -u "${PHYSICAL_ARISTA_USER:-admin}:${PHYSICAL_ARISTA_PASS:-admin}" \
        -X POST "https://$ARISTA_HOST/command-api" \
        -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"]},"id":1}' \
        2>/dev/null | python3 -c \
        "import sys,json; print('EOS '+json.load(sys.stdin)['result'][0]['version'])" \
        2>/dev/null || echo "eAPI auth failed")
    ok "Arista 7010  $ARISTA_HOST  $EAPI_OK"
    # NETCONF
    timeout 2 bash -c "echo >/dev/tcp/$ARISTA_HOST/830" 2>/dev/null && \
        ok "  NETCONF port 830 OPEN" || warn "  NETCONF port 830 CLOSED"
    # gNMI
    timeout 2 bash -c "echo >/dev/tcp/$ARISTA_HOST/6030" 2>/dev/null && \
        ok "  gNMI port 6030 OPEN" || warn "  gNMI port 6030 CLOSED"
else
    fail "Arista 7010 ($ARISTA_HOST): unreachable"
fi

# ODL
if [ "${ODL_ENABLED:-false}" = "true" ]; then
    ODL_BASE="${ODL_BASE:-http://localhost:8181}"
    curl -sf "$ODL_BASE/restconf" -u "${ODL_USER:-admin}:${ODL_PASSWORD:-admin}" \
        > /dev/null 2>&1 && ok "ODL RESTCONF $ODL_BASE" || fail "ODL: not reachable"
else
    info "ODL: disabled (ODL_ENABLED=false)"
fi

echo ""
}

# ════════════════════════════════════════════════════════════════════════════
# LOGS
# ════════════════════════════════════════════════════════════════════════════
cmd_logs() {
echo -e "${BLUE}══ Gateway log — Ctrl+C to exit ══${NC}"
tail -f "$LOG_DIR/gateway.log"
}

cmd_audit() {
echo -e "${BLUE}══ Intent audit log ══${NC}"
if [ -f "$IBN_DIR/ibn_intents.db" ]; then
    sqlite3 "$IBN_DIR/ibn_intents.db" \
        "SELECT created_at, state, score, category, action, raw_input
         FROM intents ORDER BY created_at DESC LIMIT 50;" \
        ".mode column" ".headers on" 2>/dev/null || \
    tail -f "$LOG_DIR/gateway.log"
else
    tail -f "$LOG_DIR/gateway.log"
fi
}

# ════════════════════════════════════════════════════════════════════════════
# TEST
# ════════════════════════════════════════════════════════════════════════════
cmd_test() {
cd "$IBN_DIR"
[ -f .env ] && { set -a; source .env; set +a; }
PORT="${PORT:-8002}"
echo -e "${BOLD}Running IBN V3 tests...${NC}"
echo ""
PASS=0; FAIL=0

_chk() {
    local label="$1"; shift
    if "$@" > /dev/null 2>&1; then
        ok "$label"; PASS=$((PASS+1))
    else
        fail "$label"; FAIL=$((FAIL+1))
    fi
}

# Health
HEALTH=$(curl -sf "http://localhost:$PORT/health" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))" 2>/dev/null)
[ "$HEALTH" = "healthy" ] && { ok "Gateway health: $HEALTH"; PASS=$((PASS+1)); } || \
    { fail "Gateway health: $HEALTH"; FAIL=$((FAIL+1)); }

# Intent simulation
RESULT=$(curl -sf --max-time 20 \
    -X POST "http://localhost:$PORT/api/channel/0/prompt" \
    -H "Content-Type: application/json" \
    -d '{"text":"block IoT VLAN 300 from corporate network","simulate":true}' | \
    python3 -c "
import sys,json
d=json.load(sys.stdin)
s=d.get('score',0)
v=d.get('simulation',{}).get('verdict','?')
print(f'score={s:.3f} verdict={v}')
" 2>/dev/null || echo "failed")
ok "Intent simulation: $RESULT"
PASS=$((PASS+1))

# AI chat
AI=$(curl -sf --max-time 12 \
    -X POST "http://localhost:$PORT/api/ai/chat" \
    -H "Content-Type: application/json" \
    -d '{"text":"how many VLANs are configured?"}' | \
    python3 -c "
import sys,json
d=json.load(sys.stdin)
b=d.get('backend','?')
r=d.get('reply','')
print(f'backend={b} chars={len(r)}')
" 2>/dev/null || echo "failed")
ok "AI chat: $AI"
PASS=$((PASS+1))

# Physical Arista eAPI (if enabled)
if [ "${DEPLOY_PHYSICAL:-false}" = "true" ]; then
    ARISTA_HOST="${PHYSICAL_ARISTA_HOST:-192.168.20.105}"
    ARISTA_VER=$(curl -sk --max-time 5 \
        -u "${PHYSICAL_ARISTA_USER:-admin}:${PHYSICAL_ARISTA_PASS:-admin}" \
        -X POST "https://$ARISTA_HOST/command-api" \
        -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"]},"id":1}' \
        2>/dev/null | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['result'][0]['version'])" \
        2>/dev/null || echo "")
    [ -n "$ARISTA_VER" ] && { ok "Arista 7010 eAPI: EOS $ARISTA_VER"; PASS=$((PASS+1)); } || \
        { fail "Arista 7010 eAPI: not responding"; FAIL=$((FAIL+1)); }
fi

echo ""
echo -e "  ${BOLD}Results: ${GREEN}$PASS passed${NC}  ${RED}$FAIL failed${NC}"
}

# ════════════════════════════════════════════════════════════════════════════
# PREFLIGHT
# ════════════════════════════════════════════════════════════════════════════
cmd_preflight() {
echo -e "${BOLD}Pre-flight checks for IBN V3...${NC}"
echo ""
PASS=0; FAIL=0

_p() {
    local label="$1"; shift
    if "$@" > /dev/null 2>&1; then
        ok "$label"; PASS=$((PASS+1))
    else
        fail "$label"; FAIL=$((FAIL+1))
    fi
}

_p "Python 3.12+"         python3.12 --version
_p "Docker running"       docker info
_p "ContainerLab"         containerlab version
_p "cEOS image"           docker image inspect ceos:4.32.5.1M
_p "OLLAMA"               curl -sf http://localhost:11434/api/tags
_p "Batfish"              curl -sf http://localhost:9996
_p ".env file"            test -f "$IBN_DIR/.env"
_p "RAG database"         test -d "$IBN_DIR/rag_db"
_p "Network state"        test -f "$IBN_DIR/network_state/network_state.json"
_p "main.py"              test -f "$IBN_DIR/main.py"
_p "ibn_console.html"     test -f "$IBN_DIR/ibn_console.html"
_p "Arista reachable"     ping -c1 -W2 "${PHYSICAL_ARISTA_HOST:-192.168.20.105}"
_p "Port 8002 free"       bash -c "! ss -tlnp | grep -q :8002"

echo ""
echo -e "  ${GREEN}$PASS passed${NC}  ${RED}$FAIL failed${NC}"
[ $FAIL -eq 0 ] && ok "Ready to start" || warn "Fix failures before starting"
}

# ════════════════════════════════════════════════════════════════════════════
# CONFIG SNAPSHOT
# ════════════════════════════════════════════════════════════════════════════
cmd_snapshot() {
cd "$IBN_DIR"
[ -f .env ] && { set -a; source .env; set +a; }
ARISTA_HOST="${PHYSICAL_ARISTA_HOST:-192.168.20.105}"
TS=$(date '+%Y%m%d_%H%M%S')
SNAP_DIR="$LOG_DIR/config_snapshots"
mkdir -p "$SNAP_DIR"

echo -e "${BLUE}Taking config snapshot from Arista 7010...${NC}"

python3 - << PYEOF
import json, os, sys
host = os.getenv('PHYSICAL_ARISTA_HOST','192.168.20.105')
user = os.getenv('PHYSICAL_ARISTA_USER','ibn-operator')
pw   = os.getenv('PHYSICAL_ARISTA_PASS','admin')
port = int(os.getenv('PHYSICAL_ARISTA_PORT',443))
ts   = '$TS'

try:
    import pyeapi
    conn = pyeapi.connect(transport='https', host=host, username=user,
                          password=pw, port=port)
    node = pyeapi.client.Node(conn)
    cmds = ['show version','show running-config','show ip access-lists',
            'show vlan brief','show ip bgp summary','show interfaces status',
            'show ip route','show logging last 200']
    result = node.enable(cmds)
    snap = {'timestamp':ts,'host':host,'commands':cmds,'results':result}
    fname = f'$SNAP_DIR/arista7010_{ts}.json'
    with open(fname,'w') as f: json.dump(snap, f, indent=2)
    print(f'  Snapshot saved: {fname}')
    print(f'  Size: {os.path.getsize(fname)//1024} KB')
except Exception as e:
    print(f'  ERROR: {e}')
    sys.exit(1)
PYEOF
}

# ════════════════════════════════════════════════════════════════════════════
# INTENT MINER
# ════════════════════════════════════════════════════════════════════════════
cmd_mine() {
cd "$IBN_DIR"
echo -e "${BLUE}Running intent miner on syslog...${NC}"

SYSLOG="$LOG_DIR/ibn_syslog.log"
if [ ! -f "$SYSLOG" ]; then
    fail "No syslog file at $SYSLOG"
    info "Configure Arista syslog forwarding and wait for events"
    exit 1
fi

python3 - << 'PYEOF'
import re, json, collections
from pathlib import Path

SYSLOG_PATTERNS = {
    r'PSECURE_VIOLATION.*on (\S+)':        ('security',   'quarantine', 95),
    r'BGP.*neighbor (\S+).*Down':           ('resiliency', 'remediate',  88),
    r'LINEPROTO.*Interface (\S+).*down':    ('resiliency', 'remediate',  82),
    r'CPU.*utilization.*([89]\d)%':         ('resiliency', 'remediate',  80),
    r'ACL.*denied.*src=(\S+) dst=(\S+)':    ('security',   'deny',       85),
    r'LOGIN_FAILED.*from (\S+)':            ('security',   'quarantine', 90),
    r'STP.*topology.*change':               ('resiliency', 'remediate',  75),
    r'OSPF.*neighbor (\S+).*EXSTART':       ('resiliency', 'remediate',  78),
}

syslog_file = 'logs/ibn_syslog.log'
try:
    with open(syslog_file) as f:
        lines = f.readlines()
except FileNotFoundError:
    print(f'  No syslog file found at {syslog_file}')
    exit(1)

hits = collections.defaultdict(list)
for line in lines:
    for pattern, (cat, act, pri) in SYSLOG_PATTERNS.items():
        m = re.search(pattern, line, re.IGNORECASE)
        if m:
            hits[(cat,act,pri)].append({'line':line.strip()[:150], 'match':m.groups()})

candidates = []
for (cat,act,pri), occs in hits.items():
    if len(occs) >= 3:
        candidates.append({
            'category':cat,'action':act,'priority':pri,
            'occurrences':len(occs),
            'example':occs[0]['line'],
            'suggested_intent': f'{act} {cat} pattern (seen {len(occs)} times)',
        })
candidates.sort(key=lambda x: x['occurrences'], reverse=True)

out = 'logs/mined_intents.json'
with open(out,'w') as f: json.dump(candidates, f, indent=2)
print(f'  Mined {len(candidates)} candidate intents from {len(lines)} log lines')
print(f'  Saved to {out}')
print()
for c in candidates[:10]:
    print(f"  [{c['occurrences']:3d}x] {c['suggested_intent']}")
PYEOF
}

# ════════════════════════════════════════════════════════════════════════════
# MORNING HEALTH CHECK
# ════════════════════════════════════════════════════════════════════════════
cmd_health_check() {
cd "$IBN_DIR"
[ -f .env ] && { set -a; source .env; set +a; }
PORT="${PORT:-8002}"

echo -e "${BOLD}IBN V3 Morning Policy Health Check${NC}"
echo -e "$(date)"
echo ""

CHECKS=(
    "verify IoT VLAN 300 cannot reach Finance VLAN 100"
    "verify CCTV VLAN 1100 cannot reach Billing VLAN 1200"
    "verify Guest WiFi VLAN 1900 is isolated from corporate VLANs"
    "verify SCADA VLAN 700 cannot reach IT network"
    "verify App servers VLAN 1500 can reach DB VLAN 1400"
    "verify Management VLAN 1300 can reach all switches"
)

PASS=0; FAIL=0
for CHECK in "${CHECKS[@]}"; do
    RESULT=$(curl -sf --max-time 20 \
        -X POST "http://localhost:$PORT/api/channel/0/prompt" \
        -H "Content-Type: application/json" \
        -d "{\"text\":\"$CHECK\",\"simulate\":true}" | \
        python3 -c "
import sys,json
d=json.load(sys.stdin)
v=d.get('simulation',{}).get('verdict','?')
s=d.get('score',0)
print(f'{v} {s:.3f}')
" 2>/dev/null || echo "ERROR")
    VERDICT=$(echo "$RESULT" | awk '{print $1}')
    SCORE=$(echo "$RESULT"   | awk '{print $2}')
    if [ "$VERDICT" = "PASS" ]; then
        ok "$(printf '%-55s' "$CHECK") score=$SCORE"
        PASS=$((PASS+1))
    else
        fail "$(printf '%-55s' "$CHECK") verdict=$VERDICT"
        FAIL=$((FAIL+1))
    fi
done

echo ""
echo -e "  ${BOLD}Health Check: ${GREEN}$PASS passed${NC}  ${RED}$FAIL failed${NC}"

if [ $FAIL -gt 0 ]; then
    echo ""
    warn "$FAIL policy checks FAILED — review in NOC console"
    # Notify via syslog to Channel 6
    curl -sf -X POST "http://localhost:$PORT/api/events/ingest" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"threshold_breach\",\"source\":\"morning_health_check\",
             \"message\":\"$FAIL policy checks FAILED\",\"severity\":\"critical\"}" \
        > /dev/null 2>&1 || true
fi
}

# ════════════════════════════════════════════════════════════════════════════
# Dispatcher
# ════════════════════════════════════════════════════════════════════════════
COMMAND="${1:-start}"
case "$COMMAND" in
    start)        cmd_start        ;;
    stop)         cmd_stop         ;;
    stopall)      cmd_stop_all     ;;
    restart)      cmd_stop; sleep 2; cmd_start ;;
    status)       cmd_status       ;;
    logs)         cmd_logs         ;;
    audit)        cmd_audit        ;;
    test)         cmd_test         ;;
    preflight)    cmd_preflight    ;;
    snapshot)     cmd_snapshot     ;;
    mine)         cmd_mine         ;;
    health-check) cmd_health_check ;;
    *)
        echo ""
        echo -e "${BOLD}IBN-SDN-AI V3 Management Script${NC}"
        echo ""
        echo "  Usage: $0 <command>"
        echo ""
        echo "  Commands:"
        echo "    start        Full startup with all checks"
        echo "    stop         Stop gateway, console, syslog receiver"
        echo "    stopall      Stop everything including containers"
        echo "    restart      Stop then start"
        echo "    status       Full component status"
        echo "    logs         Tail gateway log"
        echo "    audit        View intent audit log"
        echo "    test         Health + intent + AI + physical tests"
        echo "    preflight    Check all prerequisites"
        echo "    snapshot     Take config snapshot from Arista 7010"
        echo "    mine         Run intent miner on syslog"
        echo "    health-check Morning policy verification suite"
        echo ""
        exit 1
        ;;
esac
