# IBN Enterprise Scale Guide + Research Deliverables
# =====================================================

## 1. Enterprise / Corporate Scale: Open-Source Tool Stack

For a real corporate network (500+ devices, multi-site, multi-vendor):

### Topology & Inventory
| Tool | Purpose | Scale |
|------|---------|-------|
| NetBox | Source of truth for devices, IPs, VLANs, racks | Unlimited |
| LibreNMS | Topology discovery, SNMP polling, alerting | 10k+ devices |
| OpenNMS | Full-stack NMS, event correlation | Enterprise |
| Nautobot | NetBox fork with plugins, CI/CD friendly | DevNetOps |

### SDN / Automation
| Tool | Purpose |
|------|---------|
| OpenDaylight (ODL) | SDN controller — already in your stack |
| ONOS | Alternative SDN controller (telecom-grade) |
| OpenConfig + gNMI | Vendor-neutral config push (Arista, Juniper, Cisco) |
| Ansible + NAPALM | Idempotent multi-vendor config management |
| Nornir | Pure Python automation (faster than Ansible) |

### Simulation / Validation
| Tool | Purpose |
|------|---------|
| Batfish | Static config analysis — already in your stack |
| GNS3 | Full router/switch emulation (heavier than ContainerLab) |
| EVE-NG | Multi-vendor simulation (commercial + community) |
| ContainerLab | Container-based (fastest for Arista EOS) |
| pyATS / Genie | Cisco test framework — great for multi-vendor tests |

### Log Analytics / ML
| Tool | Purpose |
|------|---------|
| Elasticsearch + Kibana | Log storage and visualization |
| Apache Kafka | High-throughput log streaming (corporate scale) |
| Apache Flink | Stream processing for real-time anomaly detection |
| MLflow | ML experiment tracking and model versioning |
| Grafana | Dashboards — integrates with Zabbix, Prometheus |
| Prometheus | Metrics collection (use alongside Zabbix) |

### Enterprise additions over the digital twin
- **HashiCorp Vault** — secrets management for device credentials
- **GitLab / GitHub + CI/CD** — every intent as a git commit with pipeline
- **Keycloak** — RBAC for the IBN gateway (who can deploy vs. approve)
- **Apache Airflow** — orchestrate long-running pipeline workflows


## 2. Claude API Integration

Claude API docs: https://docs.claude.com
Model: claude-sonnet-4-20250514

The llm_router.py already implements this. Key points:

```python
# Online (Claude API)
headers = {
    "x-api-key":         os.getenv("ANTHROPIC_API_KEY"),
    "anthropic-version": "2023-06-01",
    "content-type":      "application/json",
}
body = {
    "model":      "claude-sonnet-4-20250514",
    "max_tokens": 1024,
    "system":     IBN_SYSTEM_PROMPT,
    "messages":   [{"role": "user", "content": intent_text}],
}

# Offline (OLLAMA)
ollama.chat(model="llama3", messages=[...])

# Automatic: the router probes both and picks the best available
```

Set ANTHROPIC_API_KEY in your .env for online mode.
Unset it (or set LLM_BACKEND=ollama) for offline mode.
Digital twin rule engine is the automatic worst-case fallback.


## 3. Research Deliverables Checklist

### D1: System Architecture Document
Contents:
- Full IBN pipeline diagram (already generated)
- Channel descriptions (0-5) with message flows
- Confidence scoring matrix (0.9/0.7/0.5/<0.5 routing)
- LLM integration (Claude + OLLAMA + rules fallback)
- Simulation-before-deploy gates
Format: PDF or LaTeX, ≥15 pages

### D2: Dataset
Files already generated in ibn_dataset_complete.zip:
- canonical_intents_100.json — 100 labelled intents, 10 categories
- network_state.json — switch_list, arp_table, mac_tables
- digital_twin_topology.json — 13-node topology
- digital_twin.yml — ContainerLab deployment file

### D3: ML Evaluation Report
Contents:
- CNN (1D) fault prediction accuracy, precision, recall, F1
- Random Forest baseline comparison
- Confusion matrix for fault type classification
- ROC curve and AUC score
- Training data: historical syslog events with fault labels
Tools: scikit-learn, PyTorch, MLflow for experiment tracking

### D4: Simulation Validation Report
For each of the 100 intents, document:
- Batfish reachability result (PASS/FAIL/WARN)
- ContainerLab dry-run result
- YANG validation result
- Conflict detection result
Format: CSV export from the /api/intents endpoint

### D5: Automation Demo
Live demo or recorded video showing:
1. Submit NL intent via web UI (channel 0)
2. Simulation runs and returns PASS
3. Score ≥ 0.90 → auto-deployed to ODL
4. DLUX UI shows new flow rule
5. Inject link-down event via API
6. Pipeline analyses, generates remediation intent
7. Score 0.72 → approval sent to Telegram
8. Operator approves → deployed

### D6: Comparative Literature Review
Mandatory papers (cite all):
1. RFC 9315 (Clemm 2022) — IBN definition
2. Leivadeas & Falkner (2022) IEEE Access — IBN survey
3. Mestres et al. (2017) ACM SIGCOMM — knowledge-defined networking
4. Fogel et al. (2015) NSDI — Batfish
5. Du et al. (2017) ACM CCS — DeepLog (CNN for logs)
6. He et al. (2017) ICWS — Drain3 (log parser)
7. Kreutz et al. (2015) IEEE — SDN survey
8. RFC 6241 — NETCONF
9. RFC 7950 — YANG
10. RFC 8040 — RESTCONF

### D7: Source Code Repository
Structure:
  ibn-sdn-ai/
  ├── ibn_fastapi_gateway/     (from ibn_fastapi_gateway.zip)
  ├── ibn_extras/              (from ibn_extras_complete.zip)
  ├── ibn_complete/            (from ibn_complete.zip — this file's package)
  ├── ibn_dataset_complete/    (from ibn_dataset_complete.zip)
  ├── docs/
  │   ├── architecture.md
  │   ├── api_reference.md
  │   └── deployment_guide.md
  ├── tests/
  ├── Dockerfile
  ├── docker-compose.yml
  └── README.md

### D8: Deployment Guide
Already started in SIMULATION_GUIDE.md.
Must include:
- Step-by-step ContainerLab + ODL deployment
- OLLAMA model pull instructions
- Environment variable reference
- Troubleshooting section
- Network requirements (management network, port list)


## 4. Confidence Score Routing — Full Reference

Score   | Decision          | What happens
--------|-------------------|-----------------------------------------------
≥ 0.90  | AUTO_DEPLOY       | ODL RESTCONF flow push, no human in loop
0.70-0.89| OPERATOR_APPROVAL | Telegram + web UI notification, 4h expiry
0.50-0.69| REJECT            | Returns error with specific fix recommendations
< 0.50  | FEASIBILITY_CHECK | Deep analysis report, escalation contact

Score components:
  LLM confidence    (0.0–1.0 from Claude/OLLAMA)
  + sim_adjustment  (PASS=+0.20, WARN=+0.05, FAIL=-0.30, None=-0.10)
  + conflict_adj    (no conflict=+0.05, has conflict=-0.20)
  + topology_adj    (redundant path=+0.05, no redundancy=-0.05)
  + priority_adj    ((priority-50)/1000)


## 5. Literature for Confidence-Based Automation

1. Mnih et al. (2015) — "Human-level control through deep RL" — Nature
   (confidence-driven autonomous action, same concept)

2. Ribeiro et al. (2016) — "Why Should I Trust You?: LIME" — ACM KDD
   (explainability of ML decisions — for your RCA engine)

3. Lundberg & Lee (2017) — "SHAP: A Unified Approach to Interpreting Models"
   (feature importance — explains WHY score is 0.72)

4. Varshney & Alemzadeh (2017) — "On the Safety of Machine Learning" — IEEE
   (safety-critical automation with human-in-the-loop gates)

5. Amershi et al. (2019) — "Software Engineering for ML Applications" — ICSE
   (production ML systems — relevant for your pipeline architecture)

6. Google SRE Book (2016) — Chapter 13: "Emergency Response"
   (approval workflows, runbooks — directly applicable to your pipeline)
