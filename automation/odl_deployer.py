import os, requests
from requests.auth        import HTTPBasicAuth
from models.intent_models import CanonicalIntent, DeploymentResult

ODL_BASE = os.getenv("ODL_BASE","http://localhost:8181")
ODL_AUTH = (os.getenv("ODL_USER","admin"), os.getenv("ODL_PASS","admin"))
HEADERS  = {"Content-Type":"application/json","Accept":"application/json"}

class ODLDeployer:
    def _intent_to_flow(self, intent: CanonicalIntent, flow_id: str) -> dict:
        match = {"ethernet-match":{"ethernet-type":{"type":2048}}}
        src = intent.subject.endpoint_group
        dst = intent.target.endpoint_group
        if src not in ("any","0.0.0.0/0",""):
            match["ipv4-source"]      = src if "/" in src else f"{src}/32"
        if dst not in ("any","0.0.0.0/0",""):
            match["ipv4-destination"] = dst if "/" in dst else f"{dst}/32"
        if intent.action == "deny":
            actions = [{"order":0,"drop-action":{}}]
        else:
            actions = [{"order":0,"output-action":{"output-node-connector":"NORMAL"}}]
        return {"id":flow_id,"flow-name":f"ibn-{intent.intent_id[:8]}",
                "table_id":0,"priority":intent.priority,
                "idle-timeout":0,"hard-timeout":0,
                "cookie":abs(hash(intent.intent_id))%(2**32),
                "match":match,
                "instructions":{"instruction":[{"order":0,"apply-actions":{"action":actions}}]}}

    async def deploy(self, intent: CanonicalIntent, target_nodes: list = None) -> DeploymentResult:
        if target_nodes is None:
            target_nodes = [f"openflow:{i}" for i in range(3,7)]
        pushed, failed = 0, []
        for node_id in target_nodes:
            flow_id = f"{intent.intent_id[:8]}-{node_id.replace(':','-')}"
            flow    = self._intent_to_flow(intent, flow_id)
            path    = f"/opendaylight-inventory:nodes/node/{node_id}/table/0/flow/{flow_id}"
            try:
                r = requests.put(f"{ODL_BASE}/restconf/config{path}",
                    auth=HTTPBasicAuth(*ODL_AUTH), headers=HEADERS,
                    json={"flow":[flow]}, timeout=10)
                if r.status_code in (200,201,204): pushed += 1
                else: failed.append(f"{node_id}:{r.status_code}")
            except Exception as e:
                failed.append(f"{node_id}:{str(e)[:40]}")
        return DeploymentResult(intent_id=intent.intent_id, method="odl_restconf",
            nodes_updated=target_nodes, flows_pushed=pushed,
            status="success" if not failed else "partial",
            detail=f"Pushed {pushed}/{len(target_nodes)}" + (f" failed:{failed}" if failed else ""))

class NetconfDeployer:
    async def deploy(self, intent: CanonicalIntent, devices: list = None) -> DeploymentResult:
        return DeploymentResult(intent_id=intent.intent_id, method="netconf",
            status="skipped", detail="NETCONF deployer — configure devices list in automation/odl_deployer.py")
