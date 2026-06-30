cd ~/ibn-sdn-ai-v2
source venv/bin/activate

python3 << 'EOF'
from ncclient import manager

# test Arista 7010 NETCONF
with manager.connect(
    host="192.168.20.106",
    port=830,
    username="admin",
    password="N#tAr!$T@",
    hostkey_verify=False,
    device_params={"name":"default"},
    timeout=30,
) as m:
    print(f"Connected: {m.session_id}")
    print(f"Capabilities:")
    for cap in m.server_capabilities:
        if "arista" in cap.lower() or "openconfig" in cap.lower():
            print(f"  {cap}")

    # get running config
    config = m.get_config(source="running")
    print(f"\nConfig length: {len(config.data_xml)} chars")
EOF
