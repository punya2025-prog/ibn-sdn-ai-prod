#!/bin/bash
echo "=== Starting IBN Stack ==="

# Docker services
docker start ollama batfish elasticsearch 2>/dev/null
echo "Docker services started"

# ContainerLab
sudo containerlab deploy --topo ~/ibn-sdn-ai/topology/ibn-digital-twin.clab.yml
echo "ContainerLab deployed"

# Bridge IP
sleep 10
CLAB_BR=$(brctl show | grep -v "bridge\|docker\|br0" | grep "br-" | awk '{print $1}' | head -1)
sudo ip addr add 10.200.0.1/24 dev $CLAB_BR 2>/dev/null || true
echo "Bridge IP added: $CLAB_BR"

# Container management IPs
sleep 15
declare -A NODES=(
  ["spine1"]="10.200.0.11" ["spine2"]="10.200.0.12"
  ["leaf1"]="10.200.0.21"  ["leaf2"]="10.200.0.22"
  ["leaf3"]="10.200.0.23"  ["leaf4"]="10.200.0.24"
  ["campus-core"]="10.200.0.31" ["dist1"]="10.200.0.32"
  ["dist2"]="10.200.0.33"  ["access1"]="10.200.0.41"
  ["access2"]="10.200.0.42" ["access3"]="10.200.0.43"
  ["access4"]="10.200.0.44"
)
for node in "${!NODES[@]}"; do
  IP="${NODES[$node]}"
  docker exec clab-ibn-digital-twin-$node ip addr add $IP/24 dev eth0 2>/dev/null || true
  docker exec clab-ibn-digital-twin-$node ip route add default via 10.200.0.1 2>/dev/null || true
done
echo "Container IPs configured"

# Enable SSH on switches
for node in "${!NODES[@]}"; do
  docker exec clab-ibn-digital-twin-$node Cli << EOCLI 2>/dev/null
enable
configure
management ssh
   idle-timeout 0
   authentication mode password
   no shutdown
!
end
EOCLI
done
echo "SSH enabled on all switches"

# IBN Gateway
cd ~/ibn-sdn-ai
source venv/bin/activate
export $(cat .env | xargs)
echo "Starting IBN Gateway..."
python3.12 main.py
