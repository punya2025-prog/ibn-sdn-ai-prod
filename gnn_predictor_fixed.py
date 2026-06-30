#!/usr/bin/env python3
"""
Fixed GNN predictor for ContainerLab cEOS.
Works with role-aware telemetry — BGP=0 on campus/access is correct.
"""

import json, os, sys
import torch
import torch.nn.functional as F
from torch_geometric.nn import SAGEConv
from torch_geometric.data import Data

# ── Topology edge list ─────────────────────────────────────────────────────
# Each pair (a,b) means a physical link exists between node a and node b.
# Indices match NODE_ORDER below.

NODE_ORDER = [
    "dc-spine1",    # 0
    "dc-spine2",    # 1
    "dc-leaf1",     # 2
    "dc-leaf2",     # 3
    "dc-leaf3",     # 4
    "dc-leaf4",     # 5
    "campus-core1", # 6
    "campus-dist1", # 7
    "campus-dist2", # 8
    "campus-access1",# 9
    "campus-access2",# 10
    "campus-access3",# 11
    "campus-access4",# 12
]

EDGES = [
    (0,2),(0,3),(0,4),(0,5),  # spine1 → all leaves
    (1,2),(1,3),(1,4),(1,5),  # spine2 → all leaves
    (5,6),                    # leaf4 → campus-core
    (6,7),(6,8),              # campus-core → dist1, dist2
    (7,9),(7,10),             # dist1 → access1, access2
    (8,11),(8,12),            # dist2 → access3, access4
]

FEATURE_KEYS = [
    "cpu_pct","mem_pct","vlan_count","stp_changes","bgp_sessions",
    "err_disabled","role","asn_encoded","uptime_hours","intfs_down",
]

# ── GraphSAGE model ────────────────────────────────────────────────────────
class IBNGraphSAGE(torch.nn.Module):
    def __init__(self, in_channels=10, hidden=64, out_channels=2):
        super().__init__()
        self.conv1 = SAGEConv(in_channels, hidden)
        self.conv2 = SAGEConv(hidden, hidden)
        self.conv3 = SAGEConv(hidden, out_channels)
        self.dropout = torch.nn.Dropout(0.3)

    def forward(self, x, edge_index):
        x = F.relu(self.conv1(x, edge_index))
        x = self.dropout(x)
        x = F.relu(self.conv2(x, edge_index))
        x = self.dropout(x)
        x = self.conv3(x, edge_index)
        return F.softmax(x, dim=1)

# ── Build graph from telemetry ─────────────────────────────────────────────
def build_graph(telemetry):
    node_features = []
    for node in NODE_ORDER:
        feats = telemetry.get(node, {})
        # Build 10-dim feature vector — 0.0 default for missing
        vec = [float(feats.get(k, 0.0)) for k in FEATURE_KEYS]
        node_features.append(vec)

    x = torch.tensor(node_features, dtype=torch.float)

    # Bidirectional edges
    src, dst = [], []
    for a,b in EDGES:
        src += [a,b]; dst += [b,a]
    edge_index = torch.tensor([src, dst], dtype=torch.long)

    return Data(x=x, edge_index=edge_index)

# ── Predict faults ─────────────────────────────────────────────────────────
def predict_faults(telemetry, model_path=None):
    model = IBNGraphSAGE()

    # Load saved weights if available, otherwise use random (demo mode)
    if model_path and os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path, map_location="cpu"))
        print(f"  Loaded model weights from {model_path}")
    else:
        print("  No saved model — using untrained GNN (demo mode)")
        print("  To train: run gnn_train.py with labelled fault data")

    model.eval()
    graph = build_graph(telemetry)

    with torch.no_grad():
        out = model(graph.x, graph.edge_index)

    results = []
    for i, node in enumerate(NODE_ORDER):
        fault_prob = float(out[i][1])  # probability of fault class

        # Boost probability for nodes with suspicious telemetry
        feats = telemetry.get(node, {})
        if feats.get("intfs_down", 0) > 0.5:
            fault_prob = max(fault_prob, 0.65)
        if feats.get("err_disabled", 0) > 0.2:
            fault_prob = max(fault_prob, 0.55)
        if feats.get("stp_changes", 0) > 0.3:
            fault_prob = max(fault_prob, 0.45)

        if fault_prob >= 0.70:
            risk = "HIGH"
            action = "REMEDIATE"
        elif fault_prob >= 0.40:
            risk = "medium"
            action = "monitor"
        else:
            risk = "low"
            action = "none"

        results.append({
            "node":       node,
            "fault_prob": round(fault_prob, 3),
            "risk_level": risk,
            "action":     action,
            "role":       ["spine","spine","leaf","leaf","leaf","leaf",
                           "core","dist","dist",
                           "access","access","access","access"][i],
        })

    return sorted(results, key=lambda r: -r["fault_prob"])

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    tel_path = "network_state/live_telemetry.json"
    if not os.path.exists(tel_path):
        print(f"ERROR: {tel_path} not found")
        print("Run gnn_telemetry_fix.py first to collect telemetry")
        sys.exit(1)

    with open(tel_path) as f:
        telemetry = json.load(f)

    print("Running GNN fault prediction...\n")
    results = predict_faults(telemetry)

    print(f"\n{'Node':25} {'Prob':6} {'Risk':8} {'Role':8} {'Action'}")
    print("─" * 65)
    for r in results:
        bar = "█" * int(r["fault_prob"] * 20)
        alert = " ⚠  AUTO-REMEDIATE INTENT" if r["fault_prob"] > 0.70 else ""
        print(f"  {r['node']:23} {r['fault_prob']:.3f}  "
              f"{r['risk_level']:8} {r['role']:8} {r['action']}{alert}")

    high_risk = [r for r in results if r["fault_prob"] > 0.70]
    if high_risk:
        print(f"\n{len(high_risk)} HIGH RISK node(s) detected:")
        for r in high_risk:
            print(f"  → Generating proactive remediation intent for {r['node']}")
            print(f"    Intent: 'redirect traffic away from {r['node']} "
                  f"to alternate path'")
    else:
        print("\nAll nodes within normal parameters.")

if __name__ == "__main__":
    main()
