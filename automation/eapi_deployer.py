"""
IBN eAPI Deployer — Arista EOS configuration via eAPI
Handles VLAN-based ACL deployment across all 13 cEOS switches.
"""

import asyncio, logging, os
log = logging.getLogger("ibn.eapi")

# VLAN → switch mapping (which switches carry each VLAN)
VLAN_SWITCH_MAP = {
    100: ["campus-access1","dc-leaf1"],
    200: ["campus-access1","dc-leaf1"],
    300: ["campus-access2","dc-leaf2"],
    400: ["campus-access1","dc-leaf4"],
    500: ["campus-access2","dc-leaf1"],
    600: ["campus-access3","dc-leaf2","dc-leaf4"],
    700: ["campus-access3","dc-leaf2"],
    800: ["campus-access3","dc-leaf2","dc-leaf3"],
    900: ["campus-access3","dc-leaf3"],
    1000:["campus-access1"],
    1100:["campus-access1","campus-access2","campus-access3","campus-access4","dc-leaf3"],
    1200:["campus-access1","dc-leaf3"],
    1300:["campus-access1","dc-leaf4"],
    1400:["campus-access4","dc-leaf4"],
    1500:["campus-access4","dc-leaf4"],
    1600:["campus-access4"],
    1700:["campus-access4"],
    1800:["campus-access1","campus-access2","campus-access3","campus-access4"],
    1900:["campus-access1","campus-access2"],
}

SWITCH_IPS = {
    "dc-spine1":"10.201.0.11",    "dc-spine2":"10.201.0.12",
    "dc-leaf1":"10.201.0.21",     "dc-leaf2":"10.201.0.22",
    "dc-leaf3":"10.201.0.23",     "dc-leaf4":"10.201.0.24",
    "campus-core1":"10.201.0.31", "campus-dist1":"10.201.0.32",
    "campus-dist2":"10.201.0.33", "campus-access1":"10.201.0.41",
    "campus-access2":"10.201.0.42","campus-access3":"10.201.0.43",
    "campus-access4":"10.201.0.44",
}

async def deploy_intent(intent: dict) -> dict:
    action = intent.get("action","deny")
    src    = intent.get("subject",{}).get("endpoint_group","any")
    dst    = intent.get("target",{}).get("endpoint_group","any")
    vlan   = int(intent.get("subject",{}).get("vlan",0) or 0)
    pri    = int(intent.get("priority",80))
    cat    = intent.get("category","security")

    # Determine target switches
    if vlan and vlan in VLAN_SWITCH_MAP:
        target_switches = VLAN_SWITCH_MAP[vlan]
    else:
        target_switches = ["campus-access1","campus-access2",
                           "campus-access3","campus-access4"]

    # Generate ACL name
    acl_seq  = pri
    acl_name = f"IBN-{cat.upper()[:6]}-V{vlan or 'ALL'}"

    results = []
    for sw_name in target_switches:
        ip = SWITCH_IPS.get(sw_name)
        if not ip:
            continue
        r = await _push_acl(sw_name, ip, acl_name, acl_seq, action, src, dst)
        results.append(r)

    success = sum(1 for r in results if r.get("ok"))
    return {
        "switches_attempted": len(results),
        "switches_success":   success,
        "switches_failed":    len(results) - success,
        "acl_name":           acl_name,
        "results":            results,
        "status":             "success" if success > 0 else "failed",
    }

async def _push_acl(sw_name, ip, acl_name, seq, action, src, dst):
    try:
        import pyeapi
        conn = pyeapi.connect(
            transport="http", host=ip,
            username=os.getenv("EAPI_USERNAME","admin"),
            password=os.getenv("EAPI_PASSWORD","admin"),
            port=int(os.getenv("EAPI_PORT",80))
        )
        node = pyeapi.client.Node(conn)
        cmds = [
            f"ip access-list {acl_name}",
            f"  {seq} {action} ip {src} {dst}",
            "exit",
        ]
        node.config(cmds)

        # Verify
        show = node.enable([f"show ip access-lists {acl_name}"])
        entries = show[0]["result"].get("aclList",[{}])[0].get("sequence",[])
        ok = len(entries) > 0
        log.info(f"  {sw_name}: {'OK' if ok else 'FAIL'} — {acl_name} "
                 f"({len(entries)} entries)")
        return {"switch":sw_name,"ok":ok,"entries":len(entries)}

    except Exception as e:
        log.warning(f"  {sw_name}: FAILED — {e}")
        return {"switch":sw_name,"ok":False,"error":str(e)}

async def _build_openconfig_yang(acl_name, seq, action, src, dst):
    """Generate OpenConfig YANG snippet for validation."""
    fwd = "DROP" if action=="deny" else "ACCEPT"
    return f"""
<acl-set name='{acl_name}'>
  <acl-entry sequence-id='{seq}'>
    <ipv4>
      <source-address>{src}</source-address>
      <destination-address>{dst}</destination-address>
    </ipv4>
    <actions>
      <forwarding-action>{fwd}</forwarding-action>
    </actions>
  </acl-entry>
</acl-set>"""
