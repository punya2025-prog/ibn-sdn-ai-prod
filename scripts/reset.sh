#!/bin/bash
echo "=== IBN Full Reset ==="

# 1. Stop gateway
pkill -f "python3.12" 2>/dev/null
echo "[1] Gateway stopped"
sleep 2

# 2. Clear all IBN ACLs from all switches
NODES="spine1 spine2 leaf1 leaf2 leaf3 leaf4 campus-core dist1 dist2 access1 access2 access3 access4"
for node in $NODES; do
  echo "  Clearing $node..."
  docker exec clab-ibn-digital-twin-$node Cli << EOCLI 2>/dev/null
enable
show ip access-lists summary | grep IBN
configure
no ip access-list IBN-TEST
no ip access-list IBN-IOT-BLOCK
end
write memory
EOCLI
done

# also remove any dynamically named IBN-XXXXXXXX ACLs
for node in $NODES; do
  ACLS=$(docker exec clab-ibn-digital-twin-$node Cli -c "enable
show ip access-lists summary" 2>/dev/null | grep "IBN-" | awk '{print $3}')
  for acl in $ACLS; do
    docker exec clab-ibn-digital-twin-$node Cli << EOCLI 2>/dev/null
enable
configure
no ip access-list $acl
end
write memory
EOCLI
    echo "    Removed $acl from $node"
  done
done
echo "[2] All IBN ACLs cleared from switches"

# 3. Clear ODL flows (optional)
curl -s -u admin:admin \
  -X DELETE \
  "http://localhost:8181/restconf/config/opendaylight-inventory:nodes" \
  2>/dev/null && echo "[3] ODL flows cleared" || echo "[3] ODL not running - skipped"

# 4. Clear event log file if any
rm -f /tmp/ibn_seen_emails.json
echo "[4] Email seen-file cleared"

# 5. Restart gateway fresh (all in-memory stores reset automatically)
cd ~/ibn-sdn-ai
source venv/bin/activate
export $(cat .env | xargs)
python3.12 main.py &
sleep 4

# 6. Verify
STATUS=$(curl -s http://localhost:8000/health | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
echo "[5] Gateway: $STATUS"

EVENTS=$(curl -s http://localhost:8000/api/events/stats | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])" 2>/dev/null)
echo "[6] Event log: $EVENTS events (should be 0)"

INTENTS=$(curl -s http://localhost:8000/api/intents | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])" 2>/dev/null)
echo "[7] Intent store: $INTENTS intents (should be 0)"

echo ""
echo "=== Reset complete. System at initial state. ==="
