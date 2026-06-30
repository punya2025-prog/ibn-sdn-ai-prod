"""
automation/eapi_deployer.py
Arista eAPI deployer using pyeapi config() method.
"""

import os, logging
import pyeapi
from models.intent_models import CanonicalIntent, DeploymentResult

log = logging.getLogger("ibn.eapi")

SWITCHES = {
    "spine1":      {"host": "10.201.0.11", "role": "spine"},
    "spine2":      {"host": "10.201.0.12", "role": "spine"},
    "leaf1":       {"host": "10.201.0.21", "role": "leaf"},
    "leaf2":       {"host": "10.201.0.22", "role": "leaf"},
    "leaf3":       {"host": "10.201.0.23", "role": "leaf"},
    "leaf4":       {"host": "10.201.0.24", "role": "leaf"},
    "campus-core": {"host": "10.201.0.31", "role": "core"},
    "dist1":       {"host": "10.201.0.32", "role": "distribution"},
    "dist2":       {"host": "10.201.0.33", "role": "distribution"},
    "access1":     {"host": "10.201.0.41", "role": "access"},
    "access2":     {"host": "10.201.0.42", "role": "access"},
    "access3":     {"host": "10.201.0.43", "role": "access"},
    "access4":     {"host": "10.201.0.44", "role": "access"},
}

def connect_node(host: str):
    conn = pyeapi.connect(
        transport="http",
        host=host,
        username=os.getenv("SWITCH_USER", "admin"),
        password=os.getenv("SWITCH_PASS", "admin"),
        port=80
    )
    return pyeapi.client.Node(conn)

def get_target_switches(intent: CanonicalIntent) -> list:
    # convert enum to string safely
    category = intent.category.value if hasattr(intent.category, 'value') else str(intent.category)
    if category in ("segmentation", "security", "compliance"):
        return ["leaf1","leaf2","leaf3","leaf4",
                "access1","access2","access3","access4"]
    elif category == "qos":
        return ["leaf1","leaf2","leaf3","leaf4"]
    elif category in ("resiliency","remediation"):
        return list(SWITCHES.keys())
    else:
        return ["leaf1","leaf2","leaf3","leaf4"]

def eos_ip(ip: str) -> str:
    """Convert IP to EOS ACL format."""
    import re
    if not ip or ip in ("any", "0.0.0.0/0", ""):
        return "any"
    # check if it is actually an IP address
    ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    if not re.match(ip_pattern, ip):
        return "any"   # hostname — use any
    if "/" in ip:
        return ip
    return f"host {ip}"

def intent_to_config(intent: CanonicalIntent) -> list:
    """Return config commands without configure/end."""
    # safely extract string values from enums
    action   = intent.action.value   if hasattr(intent.action,   'value') else str(intent.action)
    category = intent.category.value if hasattr(intent.category, 'value') else str(intent.category)

    src  = intent.subject.endpoint_group if intent.subject else "any"
    dst  = intent.target.endpoint_group  if intent.target  else "any"
    name = f"IBN-{intent.intent_id[:8].upper()}"

    src_eos = eos_ip(src)
    dst_eos = eos_ip(dst)

    print(f"  action={action} category={category} src={src_eos} dst={dst_eos}")

    if action == "deny":
        return [
            f"ip access-list {name}",
            f"   10 deny ip {src_eos} {dst_eos}",
            f"   20 permit ip any any",
        ]
    elif action == "allow":
        return [
            f"ip access-list {name}",
            f"   10 permit ip {src_eos} {dst_eos}",
        ]
    elif action == "prioritize":
        dscp = intent.constraints.dscp or "af21"
        return [
            f"ip access-list {name}-MATCH",
            f"   10 permit ip {src_eos} any",
            f"class-map match-any {name}",
            f"   match ip access-group {name}-MATCH",
            f"policy-map IBN-QOS",
            f"   class {name}",
            f"      set dscp {dscp}",
        ]
    elif action == "rate_limit":
        mbps = int(intent.constraints.bandwidth_mbps or 100)
        return [
            f"ip access-list {name}-MATCH",
            f"   10 permit ip {src_eos} any",
            f"policy-map IBN-RATELIMIT",
            f"   class {name}-MATCH",
            f"      police rate {mbps*1000}k",
        ]
    elif action == "quarantine":
        return [
            f"ip access-list {name}",
            f"   10 deny ip {src_eos} any",
            f"   20 deny ip any {src_eos}",
        ]
    elif action == "redirect" or action == "remediate":
        # for resiliency/redirect use route manipulation not ACL
        # skip if endpoints are hostnames not IPs
        import re
        ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        src_is_ip = bool(re.match(ip_pattern, src)) if src != "any" else True
        dst_is_ip = bool(re.match(ip_pattern, dst)) if dst != "any" else True
        if not src_is_ip or not dst_is_ip:
            return [f"! IBN {name} - resiliency intent - no ACL needed"]
        return [
            f"ip access-list {name}",
            f"   10 permit ip {src_eos} {dst_eos}",
        ]
    else:
        return [
            f"ip access-list {name}",
            f"   10 permit ip any any",
        ]


class EAPIDeployer:

    async def deploy(self, intent: CanonicalIntent) -> DeploymentResult:
        targets  = get_target_switches(intent)
        config   = intent_to_config(intent)
        pushed   = 0
        failed   = []

        log.info(f"Deploying intent {intent.intent_id[:8]} to {targets}")
        log.info(f"Config: {config}")

        for sw_name in targets:
            sw = SWITCHES.get(sw_name)
            if not sw:
                continue
            try:
                node = connect_node(sw["host"])
                node.config(config)
                node.enable(["write memory"])
                pushed += 1
                log.info(f"eAPI deployed to {sw_name} ({sw['host']})")
            except Exception as e:
                failed.append(f"{sw_name}:{str(e)[:60]}")
                log.warning(f"eAPI failed on {sw_name}: {e}")

        return DeploymentResult(
            intent_id     = intent.intent_id,
            method        = "arista_eapi",
            nodes_updated = [t for t in targets if not any(t in f for f in failed)],
            flows_pushed  = pushed,
            status        = "success" if pushed == len(targets) else
                           "partial"  if pushed > 0 else "failed",
            detail        = f"eAPI pushed to {pushed}/{len(targets)} switches"
                           + (f" | failed: {failed}" if failed else "")
        )

    async def get_topology(self) -> dict:
        topology = {}
        for sw_name, sw in SWITCHES.items():
            try:
                node   = connect_node(sw["host"])
                result = node.enable([
                    "show version",
                    "show interfaces status",
                    "show ip arp",
                    "show mac address-table"
                ])
                topology[sw_name] = {
                    "host":       sw["host"],
                    "role":       sw["role"],
                    "version":    result[0]["result"].get("version",""),
                    "model":      result[0]["result"].get("modelName",""),
                    "interfaces": result[1]["result"].get("interfaceStatuses",{}),
                    "arp_table":  result[2]["result"].get("ipV4Neighbors",[]),
                    "mac_table":  result[3]["result"].get("unicastTable",{})
                }
            except Exception as e:
                topology[sw_name] = {"host":sw["host"],"role":sw["role"],"error":str(e)}
        return topology

    async def run_command(self, sw_name: str, commands: list) -> dict:
        sw = SWITCHES.get(sw_name)
        if not sw:
            return {"error": f"Switch {sw_name} not found"}
        try:
            node   = connect_node(sw["host"])
            result = node.enable(commands)
            return {"switch": sw_name,
                    "result": [r["result"] for r in result]}
        except Exception as e:
            return {"switch": sw_name, "error": str(e)}

    async def verify_intent(self, intent: CanonicalIntent,
                             sw_name: str = "leaf2") -> dict:
        name = f"IBN-{intent.intent_id[:8].upper()}"
        sw   = SWITCHES.get(sw_name)
        if not sw:
            return {"error": f"Switch {sw_name} not found"}
        try:
            node   = connect_node(sw["host"])
            result = node.enable([f"show ip access-lists {name}"])
            return {"switch": sw_name, "acl": name,
                    "result": result[0]["result"]}
        except Exception as e:
            return {"switch": sw_name, "acl": name, "error": str(e)}
