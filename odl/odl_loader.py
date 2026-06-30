#!/usr/bin/env python3
"""
odl_loader.py
─────────────
Loads the 100-intent dataset into ODL Oxygen via RESTCONF.
Also seeds the DLUX topology with switch inventory and links.

Usage:
    python3 odl_loader.py --intents canonical_intents_100.json \
                          --topo digital_twin_topology.json \
                          --state network_state.json \
                          --odl http://172.20.0.100:8181
"""

import json
import time
import argparse
import requests
from requests.auth import HTTPBasicAuth
from uuid import uuid4

# ── ODL credentials (ODL Oxygen default) ──────────────────────────────────────
ODL_USER = "admin"
ODL_PASS = "admin"
HEADERS  = {
    "Content-Type": "application/json",
    "Accept":       "application/json"
}

def odl_auth():
    return HTTPBasicAuth(ODL_USER, ODL_PASS)

def odl_put(base_url: str, path: str, payload: dict) -> requests.Response:
    url = f"{base_url}/restconf/config{path}"
    r = requests.put(url, auth=odl_auth(), headers=HEADERS,
                     data=json.dumps(payload), timeout=10)
    return r

def odl_get(base_url: str, path: str) -> requests.Response:
    url = f"{base_url}/restconf/operational{path}"
    r = requests.get(url, auth=odl_auth(), headers=HEADERS, timeout=10)
    return r


# ── Intent → OpenFlow 1.3 flow rule ───────────────────────────────────────────
def intent_to_openflow(intent: dict) -> dict:
    """
    Maps a canonical intent action to an OF 1.3 flow.
    table 0 = ACL/segmentation/reachability/security/compliance/resiliency/vxlan
    table 1 = QoS marking / rate-limit
    """
    priority   = intent.get("priority", 50)
    action     = intent.get("action", "allow")
    flow_table = intent.get("odl_flow_table", 0)
    intent_id  = intent["intent_id"]

    src_ip = intent["subject"].get("endpoint_group", "0.0.0.0/0")
    dst_ip = intent["target"].get("endpoint_group", "0.0.0.0/0")
    ports  = intent.get("constraints", {}).get("ports", [])

    # Build match
    match = {"ipv4-source": src_ip, "ipv4-destination": dst_ip,
             "ethernet-match": {"ethernet-type": {"type": 2048}}}

    if ports:
        match["ip-match"] = {"ip-protocol": 6}   # TCP
        if len(ports) >= 1:
            match["tcp-destination-port"] = int(ports[0])

    # Build instructions based on action
    if action == "deny":
        instructions = {"instruction": [{"order": 0,
            "apply-actions": {"action": [{"order": 0, "drop-action": {}}]}}]}
    elif action == "prioritize":
        dscp_val = {"EF": 46, "AF41": 34, "AF31": 26, "AF21": 18,
                    "AF11": 10, "CS6": 48, "AF11": 10}.get(
                    intent.get("constraints", {}).get("dscp", "AF11"), 10)
        instructions = {"instruction": [{"order": 0,
            "apply-actions": {"action": [
                {"order": 0, "set-field": {"ip-match": {"ip-dscp": dscp_val}}},
                {"order": 1, "output-action": {"output-node-connector": "NORMAL"}}
            ]}}]}
    elif action == "rate_limit":
        mbps = intent.get("constraints", {}).get("bandwidth_mbps", 100)
        instructions = {"instruction": [{"order": 0,
            "apply-actions": {"action": [
                {"order": 0, "set-meter": {"meter-id": abs(hash(intent_id)) % 1000 + 1}},
                {"order": 1, "output-action": {"output-node-connector": "NORMAL"}}
            ]}}]}
    else:  # allow / redirect / default
        instructions = {"instruction": [{"order": 0,
            "apply-actions": {"action": [
                {"order": 0, "output-action": {"output-node-connector": "NORMAL"}}
            ]}}]}

    flow = {
        "id":           intent_id,
        "flow-name":    f"ibn-{intent_id}",
        "table_id":     flow_table,
        "priority":     priority,
        "idle-timeout": 0,
        "hard-timeout": 0,
        "cookie":       abs(hash(intent_id)) % (2**32),
        "match":        match,
        "instructions": instructions
    }
    return flow


# ── Push flows for all intents across all nodes ───────────────────────────────
def push_all_intents(intents_file: str, state_file: str, base_url: str):
    with open(intents_file) as f:
        intent_data = json.load(f)
    with open(state_file) as f:
        state = json.load(f)

    node_ids = [s["switch_id"] for s in state["switch_list"]]
    intents  = intent_data["intents"]

    results = {"pushed": 0, "failed": 0, "skipped": 0}

    for intent in intents:
        flow = intent_to_openflow(intent)
        table_id = flow["table_id"]

        # Push to all fabric nodes (spine-leaf intents) or campus nodes
        category = intent.get("category")
        if category in ("segmentation", "security", "reachability", "compliance"):
            target_nodes = node_ids                  # all nodes
        elif category == "qos":
            target_nodes = [n for n in node_ids if "leaf" in n or "access" in n]
        else:
            target_nodes = node_ids

        for node_id in target_nodes:
            path = (f"/opendaylight-inventory:nodes/node/{node_id}"
                    f"/table/{table_id}/flow/{flow['id']}")
            try:
                r = odl_put(base_url, path, {"flow": [flow]})
                if r.status_code in (200, 201, 204):
                    results["pushed"] += 1
                else:
                    results["failed"] += 1
                    print(f"  FAIL {intent['intent_id']} → {node_id}: {r.status_code}")
            except Exception as e:
                results["failed"] += 1
                print(f"  ERROR {intent['intent_id']} → {node_id}: {e}")

        time.sleep(0.02)  # throttle

    print(f"\n{'='*50}")
    print(f"Intent push complete: {results}")
    return results


# ── Seed DLUX topology (network-topology operational model) ───────────────────
def seed_dlux_topology(topo_file: str, state_file: str, base_url: str):
    with open(topo_file) as f:
        topo = json.load(f)
    with open(state_file) as f:
        state = json.load(f)

    # Build nodes list for ODL topology
    nodes = []
    for sw in state["switch_list"]:
        node_entry = {
            "node-id": sw["switch_id"],
            "opendaylight-inventory:id": sw["switch_id"],
            "termination-point": [
                {"tp-id": f"{sw['switch_id']}:{i}"}
                for i in range(1, sw["port_count"] + 1)
            ]
        }
        nodes.append(node_entry)

    # Build links
    links = []
    for link in topo.get("links", []):
        src_sw   = next((s for s in state["switch_list"] if s["hostname"].split("-")[-1] == link["src"] or link["src"] in s["hostname"]), None)
        dst_sw   = next((s for s in state["switch_list"] if s["hostname"].split("-")[-1] == link["dst"] or link["dst"] in s["hostname"]), None)
        if src_sw and dst_sw:
            links.append({
                "link-id": link["id"],
                "source": {"source-node": src_sw["switch_id"], "source-tp": f"{src_sw['switch_id']}:1"},
                "destination": {"dest-node": dst_sw["switch_id"], "dest-tp": f"{dst_sw['switch_id']}:1"}
            })

    topology_payload = {
        "network-topology:topology": [{
            "topology-id": "ibn-digital-twin",
            "node": nodes,
            "link": links
        }]
    }

    path = "/network-topology:network-topology/topology/ibn-digital-twin"
    r = odl_put(base_url, path, topology_payload)
    print(f"Topology seed: HTTP {r.status_code}")
    return r.status_code


# ── Check ODL is alive ─────────────────────────────────────────────────────────
def check_odl(base_url: str) -> bool:
    try:
        r = requests.get(f"{base_url}/restconf/modules", auth=odl_auth(),
                         headers=HEADERS, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="IBN ODL Oxygen dataset loader")
    parser.add_argument("--intents", default="intents/canonical_intents_100.json")
    parser.add_argument("--topo",    default="topology/digital_twin_topology.json")
    parser.add_argument("--state",   default="network_state/network_state.json")
    parser.add_argument("--odl",     default="http://172.20.0.100:8181")
    args = parser.parse_args()

    print(f"[*] Connecting to ODL Oxygen at {args.odl} ...")
    if not check_odl(args.odl):
        print("[!] ODL not reachable. Start ContainerLab first:")
        print("    sudo containerlab deploy -t topology/digital_twin.yml")
        print("    Wait ~60s for ODL to boot, then re-run this script.")
        return

    print("[+] ODL is alive.")
    print("[*] Seeding DLUX topology ...")
    seed_dlux_topology(args.topo, args.state, args.odl)

    print("[*] Pushing 100 intent flows ...")
    push_all_intents(args.intents, args.state, args.odl)

    print("\n[*] Open DLUX in browser:")
    print(f"    {args.odl}/index.html")
    print("    Login: admin / admin")
    print("    Modules to check: Topology, Yang UI, Nodes, Flows")


if __name__ == "__main__":
    main()
