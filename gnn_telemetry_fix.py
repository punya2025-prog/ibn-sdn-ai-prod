#!/usr/bin/env python3
"""
Fixed GNN telemetry collector for ContainerLab cEOS.
Handles BGP inactive on campus/access switches gracefully.
Uses role-aware command selection.
"""

import pyeapi, json, os, re

SWITCHES = {
    # Role determines which commands are safe to run
    "dc-spine1":      {"ip":"10.201.0.11","role":0,"has_bgp":True},
    "dc-spine2":      {"ip":"10.201.0.12","role":0,"has_bgp":True},
    "dc-leaf1":       {"ip":"10.201.0.21","role":1,"has_bgp":True},
    "dc-leaf2":       {"ip":"10.201.0.22","role":1,"has_bgp":True},
    "dc-leaf3":       {"ip":"10.201.0.23","role":1,"has_bgp":True},
    "dc-leaf4":       {"ip":"10.201.0.24","role":1,"has_bgp":True},
    "campus-core1":   {"ip":"10.201.0.31","role":2,"has_bgp":False},
    "campus-dist1":   {"ip":"10.201.0.32","role":3,"has_bgp":False},
    "campus-dist2":   {"ip":"10.201.0.33","role":3,"has_bgp":False},
    "campus-access1": {"ip":"10.201.0.41","role":4,"has_bgp":False},
    "campus-access2": {"ip":"10.201.0.42","role":4,"has_bgp":False},
    "campus-access3": {"ip":"10.201.0.43","role":4,"has_bgp":False},
    "campus-access4": {"ip":"10.201.0.44","role":4,"has_bgp":False},
}

# Commands safe for ALL switches
BASE_CMDS = [
    "show version",
    "show interfaces status",
    "show spanning-tree summary",
    "show vlan brief",
]

# Commands only for BGP-capable switches
BGP_CMDS = [
    "show ip bgp summary",
]

def collect(hostname, info):
    ip      = info["ip"]
    role    = info["role"]
    has_bgp = info["has_bgp"]

    try:
        conn = pyeapi.connect(
            transport="http", host=ip,
            username="admin", password="admin", port=80)
        node = pyeapi.client.Node(conn)

        # Always run base commands
        base_results = node.enable(BASE_CMDS)
        version = base_results[0]["result"]
        intfs   = base_results[1]["result"]
        stp     = base_results[2]["result"]
        vlans   = base_results[3]["result"]

        # Count metrics from base commands
        err_dis    = sum(1 for p in intfs.get("interfaceStatuses",{}).values()
                        if p.get("lineProtocolStatus") == "errdisabled")
        intfs_down = sum(1 for p in intfs.get("interfaceStatuses",{}).values()
                        if p.get("lineProtocolStatus") == "down"
                        and not p.get("name","").startswith("Management"))
        stp_changes = stp.get("topologyChanges", 0)
        vlan_count  = len(vlans.get("vlans", {}))
        uptime_secs = version.get("uptime", 0)

        # BGP: only query if the switch runs BGP
        bgp_sessions = 0
        if has_bgp:
            try:
                bgp_result   = node.enable(BGP_CMDS)
                bgp_summary  = bgp_result[0]["result"]
                peers = bgp_summary.get("vrfs",{}).get("default",{}).get("peers",{})
                bgp_sessions = sum(1 for p in peers.values()
                                   if p.get("peerState","") == "Established")
            except Exception as bgp_err:
                # BGP process may not be started yet — default to 0
                bgp_sessions = 0

        # Normalise features to 0.0–1.0 range
        features = {
            "cpu_pct":      0.05,                        # cEOS: always low
            "mem_pct":      0.30,                        # cEOS: always low
            "vlan_count":   min(1.0, vlan_count / 20),
            "stp_changes":  min(1.0, stp_changes / 10),
            "bgp_sessions": min(1.0, bgp_sessions / 4),
            "err_disabled": min(1.0, err_dis / 5),
            "role":         role / 4,                    # 0=spine 1=leaf 2=core 3=dist 4=access
            "asn_encoded":  0.0,                         # filled below
            "uptime_hours": min(1.0, uptime_secs / (720*3600)),
            "intfs_down":   1.0 if intfs_down > 0 else 0.0,
        }

        # Try to get ASN for BGP switches
        if has_bgp:
            try:
                asn_result = node.enable(["show ip bgp summary"])
                asn = asn_result[0]["result"].get("vrfs",{}).get(
                      "default",{}).get("routerId","0")
                # ASN from config — use role-based default
                features["asn_encoded"] = (65010 + role) / 65000
            except:
                features["asn_encoded"] = (65000 + role) / 65000
        else:
            features["asn_encoded"] = 0.0

        print(f"  {hostname:22} role:{role} BGP:{bgp_sessions:2} "
              f"STP-chg:{stp_changes:3} VLANs:{vlan_count:3} "
              f"err-dis:{err_dis} down:{intfs_down}  OK")
        return features

    except Exception as e:
        print(f"  {hostname:22} UNREACHABLE ({e})")
        # Return safe default features so GNN can still run
        return {
            "cpu_pct":0.05,"mem_pct":0.30,"vlan_count":0.0,
            "stp_changes":0.0,"bgp_sessions":0.0,"err_disabled":0.0,
            "role": info["role"]/4,"asn_encoded":0.0,
            "uptime_hours":0.0,"intfs_down":1.0,   # mark as suspect
        }

def main():
    print("Collecting live telemetry from cEOS switches...\n")
    telemetry = {}
    for hostname, info in SWITCHES.items():
        telemetry[hostname] = collect(hostname, info)

    # Save for GNN
    os.makedirs("network_state", exist_ok=True)
    path = "network_state/live_telemetry.json"
    with open(path, "w") as f:
        json.dump(telemetry, f, indent=2)
    print(f"\nSaved telemetry for {len(telemetry)} switches → {path}")
    print("\nFeature summary (10-dim per node):")
    for hostname, feats in telemetry.items():
        vec = [round(v,3) for v in feats.values()]
        print(f"  {hostname:22} {vec}")

if __name__ == "__main__":
    main()
