#!/usr/bin/env bash
# =============================================================================
# IBN Full Stack Setup Script
# Installs: ContainerLab, ODL Oxygen, Batfish, OLLAMA, FastAPI gateway,
#           Ansible, email watcher, Telegram bot, Zabbix sender
#
# Usage:
#   chmod +x setup_all.sh
#   sudo ./setup_all.sh
#
# Tested on Ubuntu 22.04 LTS
# =============================================================================

set -euo pipefail

IBN_HOME="/opt/ibn"
LOG="$IBN_HOME/install.log"
PYTHON="python3"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*" | tee -a "$LOG"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*" | tee -a "$LOG"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "$LOG"; }
die()   { echo -e "${RED}[FAIL]${NC} $*" | tee -a "$LOG"; exit 1; }

# ── Pre-flight ──────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root: sudo ./setup_all.sh"
mkdir -p "$IBN_HOME" "$IBN_HOME/logs" "$IBN_HOME/backups"
info "IBN home: $IBN_HOME"


# ── 1. System packages ───────────────────────────────────────────────────────
info "Installing system packages…"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    docker.io docker-compose \
    git curl wget jq zip unzip \
    iproute2 net-tools \
    libssl-dev libffi-dev \
    >> "$LOG" 2>&1
systemctl enable --now docker >> "$LOG" 2>&1
ok "System packages installed"


# ── 2. ContainerLab ─────────────────────────────────────────────────────────
info "Installing ContainerLab…"
if ! command -v containerlab &>/dev/null; then
    bash -c "$(curl -sL https://get.containerlab.dev)" >> "$LOG" 2>&1
    ok "ContainerLab installed: $(containerlab version | head -1)"
else
    ok "ContainerLab already installed"
fi


# ── 3. Batfish (Docker) ──────────────────────────────────────────────────────
info "Starting Batfish container…"
if ! docker ps --format '{{.Names}}' | grep -q batfish; then
    docker run -d --name batfish \
        --restart unless-stopped \
        -p 9997:9997 -p 9996:9996 \
        batfish/allinone >> "$LOG" 2>&1
    ok "Batfish started on ports 9996/9997"
else
    ok "Batfish already running"
fi


# ── 4. Elasticsearch + Kibana (hot log store + visualization) ───────────────
info "Starting Elasticsearch + Kibana…"
if ! docker ps --format '{{.Names}}' | grep -q elasticsearch; then
    docker run -d --name elasticsearch \
        --restart unless-stopped \
        -p 9200:9200 \
        -e "discovery.type=single-node" \
        -e "xpack.security.enabled=false" \
        elasticsearch:8.12.0 >> "$LOG" 2>&1
    ok "Elasticsearch started on :9200"

    docker run -d --name kibana \
        --restart unless-stopped \
        --link elasticsearch \
        -p 5601:5601 \
        kibana:8.12.0 >> "$LOG" 2>&1
    ok "Kibana started on :5601"
else
    ok "Elasticsearch already running"
fi


# ── 5. ClickHouse (cold archive for ML training data) ───────────────────────
info "Starting ClickHouse…"
if ! docker ps --format '{{.Names}}' | grep -q clickhouse; then
    docker run -d --name clickhouse \
        --restart unless-stopped \
        -p 9000:9000 -p 8123:8123 \
        clickhouse/clickhouse-server >> "$LOG" 2>&1
    ok "ClickHouse started on :9000"
else
    ok "ClickHouse already running"
fi


# ── 6. ODL Oxygen (containerised) ───────────────────────────────────────────
info "Starting OpenDaylight Oxygen…"
if ! docker ps --format '{{.Names}}' | grep -q opendaylight; then
    docker run -d --name opendaylight \
        --restart unless-stopped \
        -p 8181:8181 \
        -p 6633:6633 \
        -p 8101:8101 \
        opendaylight/odl:0.8.4 >> "$LOG" 2>&1
    ok "ODL Oxygen started — DLUX: http://localhost:8181/index.html"
    info "  Waiting 45s for ODL to fully boot…"
    sleep 45
    # Install DLUX feature
    docker exec opendaylight /opt/opendaylight/bin/client \
        "feature:install odl-l2switch-switch-ui odl-restconf-all odl-dlux-core odl-dluxapps-topology odl-mdsal-apidocs" \
        >> "$LOG" 2>&1 || warn "ODL feature install may need retry after boot"
else
    ok "ODL already running"
fi


# ── 7. OLLAMA (offline LLM) ──────────────────────────────────────────────────
info "Installing OLLAMA…"
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.ai/install.sh | sh >> "$LOG" 2>&1
    ok "OLLAMA installed"
else
    ok "OLLAMA already installed"
fi

info "Pulling llama3 model (this may take a few minutes)…"
ollama pull llama3 >> "$LOG" 2>&1 &
OLLAMA_PID=$!
ok "llama3 pull started in background (PID $OLLAMA_PID)"


# ── 8. Python virtual environment + all packages ────────────────────────────
info "Creating Python venv at $IBN_HOME/venv…"
$PYTHON -m venv "$IBN_HOME/venv" >> "$LOG" 2>&1
source "$IBN_HOME/venv/bin/activate"

info "Installing Python packages (this takes a few minutes)…"
pip install --upgrade pip >> "$LOG" 2>&1
pip install \
    fastapi uvicorn[standard] python-multipart pydantic httpx \
    pyyaml requests \
    ncclient netmiko \
    ollama \
    torch scikit-learn numpy pandas joblib \
    drain3 python-dateutil \
    elasticsearch clickhouse-driver \
    pybatfish \
    pyang \
    py-zabbix \
    python-telegram-bot \
    twilio \
    imapclient \
    ansible ansible-lint \
    >> "$LOG" 2>&1

# Arista Ansible collections
ansible-galaxy collection install arista.eos ansible.netcommon >> "$LOG" 2>&1
ok "Python packages and Ansible collections installed"


# ── 9. rsyslog filter config ─────────────────────────────────────────────────
info "Configuring rsyslog for IBN (severity filter: critical/error/warning)…"
cat > /etc/rsyslog.d/50-ibn.conf << 'RSYSLOG'
# IBN syslog filter — forward only severity 0-4 to Elasticsearch via Logstash
:msg, contains, "ERRDISABLE"      /var/log/ibn/network_critical.log
:msg, contains, "LINEPROTO"       /var/log/ibn/network_critical.log
:msg, contains, "BGP-5-ADJCHANGE" /var/log/ibn/network_critical.log
:msg, contains, "STP"             /var/log/ibn/network_critical.log
:msg, contains, "CPUHOG"          /var/log/ibn/network_critical.log
# Forward to Logstash
*.warn  @@127.0.0.1:5044
RSYSLOG
mkdir -p /var/log/ibn
systemctl restart rsyslog >> "$LOG" 2>&1 || warn "rsyslog restart failed — check manually"
ok "rsyslog configured"


# ── 10. Deploy ContainerLab digital twin ─────────────────────────────────────
if [[ -f "topology/digital_twin.yml" ]]; then
    info "Deploying ContainerLab digital twin…"
    containerlab deploy --topo topology/digital_twin.yml >> "$LOG" 2>&1
    ok "Digital twin deployed"
else
    warn "topology/digital_twin.yml not found — copy from ibn_dataset_complete.zip first"
fi


# ── 11. Systemd services ──────────────────────────────────────────────────────
info "Installing systemd services…"

# IBN FastAPI gateway
cat > /etc/systemd/system/ibn-gateway.service << SERVICE
[Unit]
Description=IBN Multi-Channel FastAPI Gateway
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$IBN_HOME
ExecStart=$IBN_HOME/venv/bin/python3 main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=ODL_BASE=http://localhost:8181
Environment=BATFISH_HOST=localhost
Environment=OLLAMA_HOST=http://localhost:11434

[Install]
WantedBy=multi-user.target
SERVICE

# IMAP watcher
cat > /etc/systemd/system/ibn-email-watcher.service << SERVICE
[Unit]
Description=IBN Email IMAP Watcher (Channel 2)
After=ibn-gateway.service

[Service]
Type=simple
User=root
WorkingDirectory=$IBN_HOME/email_watcher
ExecStart=$IBN_HOME/venv/bin/python3 imap_watcher.py --config watcher_config.yml
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

# Telegram bot
cat > /etc/systemd/system/ibn-telegram-bot.service << SERVICE
[Unit]
Description=IBN Telegram Bot (Channel 4)
After=ibn-gateway.service

[Service]
Type=simple
User=root
WorkingDirectory=$IBN_HOME/telegram_bot
ExecStart=$IBN_HOME/venv/bin/python3 ibn_bot.py --config bot_config.yml
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable ibn-gateway ibn-email-watcher ibn-telegram-bot
ok "Systemd services registered"


# ── 12. Zabbix media type for IBN ─────────────────────────────────────────────
info "Zabbix integration notes written to $IBN_HOME/ZABBIX_SETUP.md"
cat > "$IBN_HOME/ZABBIX_SETUP.md" << 'ZABBIX'
# Zabbix → IBN Gateway Integration

## 1. Create Media Type (Webhook)
Administration → Media types → Create
  Name:         IBN Gateway
  Type:         Webhook
  Parameters:
    - trigger_name:  {TRIGGER.NAME}
    - host:          {HOST.NAME}
    - severity:      {TRIGGER.SEVERITY}
    - status:        {TRIGGER.STATUS}
    - ip:            {HOST.IP}
  Script:
    var req = new HttpRequest();
    req.addHeader('Content-Type: application/json');
    req.post('http://localhost:8000/api/zabbix/alert',
      JSON.stringify({
        trigger_name: value.trigger_name,
        host:         value.host,
        severity:     value.severity,
        status:       value.status,
        ip:           value.ip
      })
    );
    return 'sent';

## 2. Create IBN items on each host (for ML predictions)
Configuration → Hosts → Items → Create item
  Name:  IBN fault probability
  Key:   ibn.fault.prediction
  Type:  Zabbix trapper
  Units: %

  Name:  IBN predicted fault type
  Key:   ibn.fault.type
  Type:  Zabbix trapper

  Name:  IBN root cause
  Key:   ibn.fault.rca_cause
  Type:  Zabbix trapper

## 3. Trigger
  Name: IBN ML fault prediction high
  Expression: {HOST:ibn.fault.prediction.last()}>80
  Severity: High
ZABBIX
ok "Zabbix setup guide written"


# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          IBN Stack Setup Complete                            ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  FastAPI Gateway:  http://localhost:8000                     ║"
echo "║  API Docs:         http://localhost:8000/api/docs            ║"
echo "║  ODL DLUX:         http://localhost:8181/index.html          ║"
echo "║  Kibana:           http://localhost:5601                     ║"
echo "║  Batfish:          localhost:9997                            ║"
echo "║  Elasticsearch:    http://localhost:9200                     ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                                 ║"
echo "║  1. Copy ibn_fastapi_gateway/ files to /opt/ibn/             ║"
echo "║  2. Edit email_watcher/watcher_config.yml with credentials   ║"
echo "║  3. Edit telegram_bot/bot_config.yml with your bot token     ║"
echo "║  4. sudo systemctl start ibn-gateway                         ║"
echo "║  5. sudo systemctl start ibn-email-watcher ibn-telegram-bot  ║"
echo "║  6. Open http://localhost:8000 to start submitting intents   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
