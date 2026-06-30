#!/usr/bin/env python3
"""
event_generator.py
===================
Generates synthetic syslog events and pushes them to the IBN gateway
for testing the NOC dashboard, ML predictor, and pipeline.

Modes:
  --mode random     : continuous random events (default)
  --mode scenario   : pre-built fault scenarios (link_down, errdisable, bgp_storm)
  --mode replay     : replay events from a log file
  --mode manual     : single event from CLI args

Run:
  python3 event_generator.py --mode random --rate 2
  python3 event_generator.py --mode scenario --scenario link_down
  python3 event_generator.py --mode manual --host dc-leaf2 --severity crit --msg "ERRDISABLE on Eth3"
"""

import asyncio, httpx, random, argparse, json, time, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [EVENT-GEN] %(levelname)s %(message)s")
log = logging.getLogger("event_gen")

GATEWAY = "http://localhost:8000"

HOSTS = [
    "dc-spine1","dc-spine2",
    "dc-leaf1","dc-leaf2","dc-leaf3","dc-leaf4",
    "campus-core1","campus-dist1","campus-dist2",
    "campus-access1","campus-access2","campus-access3","campus-access4",
]

SYSLOG_EVENTS = {
    "crit": [
        ("%ETH-4-ERRDISABLE: Interface {intf} disabled due to port-security violation", "err_disabled"),
        ("%LINEPROTO-5-UPDOWN: Interface {intf} changed state to down",               "interface_down"),
        ("%BGP-5-ADJCHANGE: neighbor {peer} Down Hold Timer Expired",                  "bgp_down"),
        ("%OSPF-5-ADJCHG: Process 1 Nbr {peer} on {intf} from FULL to DOWN",          "ospf_down"),
        ("%LACP-5-BUNDLEDOWN: Port-Channel{num} is down — min-links not met",          "lacp_fault"),
        ("%FAN-4-FANDOWN: Fan {num} has failed",                                       "hardware_fault"),
    ],
    "warn": [
        ("%STP-6-TOPOLOGY_CHANGE: VLAN {vlan} topology change notification received",  "stp_change"),
        ("%CPUHOG-4: CPU utilization {pct}% exceeds threshold",                        "cpu_stress"),
        ("%SEC-6-IPACCESSLOGP: list IBN deny {proto} {src}({sport}) -> {dst}({dport})","sec_violation"),
        ("%TUNNEL-4: VXLAN tunnel {vni} to {peer} packet drop rate {pct}%",            "tunnel_fault"),
        ("%BGP-4-ADJCHANGE: neighbor {peer} passive session reset",                    "bgp_down"),
        ("%STP-4-BPDUGUARD: BPDU received on port {intf} with BPDU guard enabled",    "stp_change"),
    ],
    "info": [
        ("%BGP-5-ADJCHANGE: neighbor {peer} Up",                                       None),
        ("%LINEPROTO-5-UPDOWN: Interface {intf} changed state to up",                  None),
        ("%OPENSSH-6-AUTH: User admin authenticated from {src}",                       None),
        ("%GNMI-6-SUBSCRIPTION: gNMI subscription from {src} established",             None),
        ("%CONFIG-6-SAVE: Running config saved to flash",                              None),
    ],
}

INTERFACES = ["Ethernet1","Ethernet2","Ethernet3","Ethernet4","Port-Channel1"]
VLANS      = [100,200,300,400,500,600,700,800,900,1200,1300]
PEERS      = ["10.0.0.1","10.0.0.3","10.0.0.5","10.0.0.7","10.255.0.1","10.255.0.2"]
PROTOS     = ["tcp","udp","icmp"]

SCENARIOS = {
    "link_down": [
        {"host":"dc-leaf2","severity":"warn","label":"stp_change",    "msg":"%STP-6-TOPOLOGY_CHANGE: VLAN 300 topology change"},
        {"host":"dc-leaf2","severity":"warn","label":"stp_change",    "msg":"%STP-6-TOPOLOGY_CHANGE: VLAN 600 topology change"},
        {"host":"dc-leaf2","severity":"crit","label":"interface_down","msg":"%LINEPROTO-5-UPDOWN: Ethernet1 changed state to down"},
        {"host":"dc-spine1","severity":"warn","label":"bgp_down",     "msg":"%BGP-5-ADJCHANGE: neighbor 10.0.0.3 Down"},
        {"host":"dc-leaf3","severity":"warn","label":"interface_down","msg":"%LINEPROTO-5-UPDOWN: Ethernet1 changed state to down"},
    ],
    "errdisable": [
        {"host":"dc-leaf2","severity":"warn","label":"sec_violation", "msg":"%SEC-6-IPACCESSLOGP: deny tcp 10.10.3.5 → 10.10.0.1"},
        {"host":"dc-leaf2","severity":"warn","label":"sec_violation", "msg":"%SEC-6-IPACCESSLOGP: deny tcp 10.10.3.6 → 10.10.0.1"},
        {"host":"dc-leaf2","severity":"warn","label":"sec_violation", "msg":"%SEC-6-IPACCESSLOGP: deny tcp 10.10.3.7 → 10.10.0.1"},
        {"host":"dc-leaf2","severity":"crit","label":"err_disabled",  "msg":"%ETH-4-ERRDISABLE: Ethernet3 disabled due to port-security"},
    ],
    "bgp_storm": [
        {"host":"dc-spine1","severity":"warn","label":"cpu_stress",   "msg":"%CPUHOG-4: CPU utilization 87%"},
        {"host":"dc-spine1","severity":"warn","label":"cpu_stress",   "msg":"%CPUHOG-4: CPU utilization 91%"},
        {"host":"dc-spine1","severity":"crit","label":"bgp_down",     "msg":"%BGP-5-ADJCHANGE: neighbor 10.0.0.1 Down Hold Timer Expired"},
        {"host":"dc-spine1","severity":"crit","label":"bgp_down",     "msg":"%BGP-5-ADJCHANGE: neighbor 10.0.0.3 Down Hold Timer Expired"},
        {"host":"dc-leaf1", "severity":"crit","label":"interface_down","msg":"%LINEPROTO-5-UPDOWN: Vlan100 changed state to down"},
    ],
    "compliance_breach": [
        {"host":"campus-access4","severity":"warn","label":"sec_violation","msg":"%SEC-6-IPACCESSLOGP: deny tcp 10.10.12.5 → 10.10.5.1 (billing→dev)"},
        {"host":"campus-access4","severity":"warn","label":"sec_violation","msg":"%SEC-6-IPACCESSLOGP: deny tcp 10.10.5.3 → 10.10.12.1 (dev→billing)"},
        {"host":"campus-dist2",  "severity":"warn","label":"stp_change",   "msg":"%STP-6-TOPOLOGY_CHANGE: VLAN 1200 topology change (PCI zone)"},
    ],
    "hardware_fault": [
        {"host":"dc-leaf3","severity":"warn","label":"hardware_fault","msg":"%FAN-4-FANDOWN: Fan 1 has failed — thermal risk"},
        {"host":"dc-leaf3","severity":"warn","label":"cpu_stress",    "msg":"%CPUHOG-4: CPU utilization 78% due to thermal throttle"},
        {"host":"dc-leaf3","severity":"crit","label":"interface_down","msg":"%LINEPROTO-5-UPDOWN: Ethernet1 changed state to down"},
        {"host":"dc-leaf3","severity":"crit","label":"interface_down","msg":"%LINEPROTO-5-UPDOWN: Ethernet2 changed state to down"},
    ],
}


def fill_template(tmpl: str) -> str:
    return (tmpl
        .replace("{intf}",   random.choice(INTERFACES))
        .replace("{vlan}",   str(random.choice(VLANS)))
        .replace("{peer}",   random.choice(PEERS))
        .replace("{src}",    f"10.10.{random.randint(1,13)}.{random.randint(1,50)}")
        .replace("{dst}",    f"10.10.{random.randint(1,13)}.{random.randint(1,50)}")
        .replace("{sport}",  str(random.randint(1024,65535)))
        .replace("{dport}",  str(random.choice([80,443,22,3306,5432])))
        .replace("{pct}",    str(random.randint(70,95)))
        .replace("{num}",    str(random.randint(1,4)))
        .replace("{vni}",    str(random.choice([10001,10002,10003,10006])))
        .replace("{proto}",  random.choice(PROTOS))
    )


async def send_event(event: dict) -> dict:
    """Send a single event to the IBN gateway pipeline."""
    payload = {
        "type":      event.get("type", "syslog"),
        "source":    event.get("host", "unknown"),
        "message":   event.get("msg",  ""),
        "severity":  event.get("severity", "info"),
        "label":     event.get("label"),
        "channel":   0,
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{GATEWAY}/api/pipeline/event", json=payload)
            return r.json()
    except Exception as e:
        log.warning(f"Gateway error: {e}")
        return {}


async def send_to_predict(host: str, events: list) -> dict:
    """Send events to the ML predictor."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{GATEWAY}/api/predict",
                json={"host": host, "events": events})
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def mode_random(rate: float, count: int):
    """Emit random events continuously."""
    log.info(f"Random mode: {rate} events/sec")
    sent = 0
    while count == 0 or sent < count:
        sev = random.choices(
            ["crit","warn","info"],
            weights=[15, 35, 50]
        )[0]
        tmpl, label = random.choice(SYSLOG_EVENTS[sev])
        host = random.choice(HOSTS)
        msg  = fill_template(tmpl)
        event = {"host": host, "severity": sev, "msg": msg, "label": label}
        result = await send_event(event)
        log.info(f"[{sev.upper():4}] {host:20} {msg[:60]}")
        sent += 1
        await asyncio.sleep(1.0 / rate)


async def mode_scenario(scenario: str, delay: float):
    """Replay a fault scenario with realistic timing."""
    events = SCENARIOS.get(scenario)
    if not events:
        log.error(f"Unknown scenario: {scenario}. Available: {list(SCENARIOS.keys())}")
        return

    log.info(f"Scenario: {scenario} ({len(events)} events)")
    predict_events = []
    for i, ev in enumerate(events):
        log.info(f"[{i+1}/{len(events)}] {ev['severity'].upper():4} {ev['host']:20} {ev['msg'][:60]}")
        result = await send_event({**ev, "type": "syslog"})
        predict_events.append({"label": ev.get("label","unknown"), "severity": {"crit":2,"warn":4,"info":6}.get(ev["severity"],4)})
        if i < len(events)-1:
            await asyncio.sleep(delay)

    # Run ML prediction after scenario
    log.info("\nRunning ML fault prediction...")
    host = events[-1]["host"]
    pred = await send_to_predict(host, predict_events)
    log.info(f"Prediction for {host}:")
    log.info(f"  Fault probability: {pred.get('fault_probability',0)*100:.1f}%")
    log.info(f"  Predicted fault:   {pred.get('predicted_fault','none')}")
    if pred.get('rca'):
        log.info(f"  Root cause:        {pred['rca'].get('cause','?')}")
        log.info(f"  RCA confidence:    {pred['rca'].get('confidence',0)*100:.0f}%")


async def mode_manual(host: str, severity: str, msg: str, label: str):
    """Send a single manually specified event."""
    event = {"host": host, "severity": severity, "msg": msg, "label": label}
    log.info(f"Sending: [{severity.upper()}] {host}: {msg}")
    result = await send_event(event)
    log.info(f"Result: {json.dumps(result, indent=2)[:300]}")


async def mode_replay(filepath: str, delay: float):
    """Replay events from a JSON or syslog file."""
    import pathlib
    p = pathlib.Path(filepath)
    if not p.exists():
        log.error(f"File not found: {filepath}")
        return
    if filepath.endswith(".json"):
        events = json.loads(p.read_text())
    else:
        events = []
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            events.append({"host":"replay","severity":"warn","msg":line[:200],"label":"unknown"})
    log.info(f"Replaying {len(events)} events from {filepath}")
    for ev in events:
        await send_event(ev)
        log.info(f"  Replayed: {ev.get('msg','')[:60]}")
        await asyncio.sleep(delay)


async def main():
    parser = argparse.ArgumentParser(description="IBN Event Generator")
    parser.add_argument("--gateway",  default="http://localhost:8000")
    parser.add_argument("--mode",     default="random",
                        choices=["random","scenario","manual","replay"])
    parser.add_argument("--rate",     type=float, default=1.0,
                        help="Events per second (random mode)")
    parser.add_argument("--count",    type=int,   default=0,
                        help="Number of events (0=infinite)")
    parser.add_argument("--scenario", default="link_down",
                        choices=list(SCENARIOS.keys()))
    parser.add_argument("--delay",    type=float, default=1.5,
                        help="Delay between scenario events (seconds)")
    parser.add_argument("--host",     default="dc-leaf2")
    parser.add_argument("--severity", default="crit",
                        choices=["crit","warn","info"])
    parser.add_argument("--msg",      default="%LINEPROTO-5-UPDOWN: Ethernet1 changed state to down")
    parser.add_argument("--label",    default="interface_down")
    parser.add_argument("--file",     default="events.json",
                        help="File to replay (replay mode)")
    args = parser.parse_args()

    global GATEWAY
    GATEWAY = args.gateway

    print(f"\nIBN Event Generator — gateway: {GATEWAY}\n{'='*50}")

    if args.mode == "random":
        await mode_random(args.rate, args.count)
    elif args.mode == "scenario":
        await mode_scenario(args.scenario, args.delay)
    elif args.mode == "manual":
        await mode_manual(args.host, args.severity, args.msg, args.label)
    elif args.mode == "replay":
        await mode_replay(args.file, args.delay)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nEvent generator stopped.")
