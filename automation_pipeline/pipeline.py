import asyncio, json, logging, os
from datetime import datetime
from typing   import Optional

log = logging.getLogger("ibn.pipeline")

from llm_layer.llm_router               import get_router
from topology_analysis.topology_analyzer import TopologyAnalyzer, FaultAnalysis
from confidence_engine.scorer            import score_intent, build_approval_request, Decision

PENDING_APPROVALS: dict = {}
INTENT_STORE:      dict = {}

class IBNPipeline:
    def __init__(self):
        self.router   = get_router()
        self.topology = TopologyAnalyzer()
        self.topology.load()

    async def process_event(self, event: dict) -> dict:
        event_type = event.get("type","user_intent")
        log.info(f"Pipeline event: {event_type}")
        if event_type == "link_down":
            return await self._handle_link_down(event)
        return await self._handle_generic_intent(event)

    async def _handle_link_down(self, event: dict) -> dict:
        src  = event.get("source","unknown")
        dst  = event.get("peer","unknown")
        intf = event.get("interface","")
        try:    fault = self.topology.analyse_fault(src, dst, "down")
        except: fault = None
        llm_analysis = await self.router.analyse_link_down({**event,
            "affected_vlans": fault.affected_vlans if fault else [],
            "affected_zones": fault.affected_zones if fault else [],
            "has_redundancy": fault.has_redundancy if fault else False})
        from uuid import uuid4
        intent_id = str(uuid4())
        rem       = llm_analysis.get("remediation_intent",{})
        canonical = {"intent_id":intent_id,"channel":event.get("channel",0),
            "category":rem.get("category","resiliency"),
            "action":rem.get("action","redirect"),"priority":88,
            "subject":rem.get("subject",{"endpoint_group":src}),
            "target":rem.get("target",{"endpoint_group":dst}),
            "constraints":rem.get("constraints",{}),
            "description":f"Auto-remediation: link down {src} {intf}"}
        return await self._run_pipeline(canonical, fault, llm_analysis)

    async def _handle_generic_intent(self, event: dict) -> dict:
        text   = event.get("message") or event.get("text") or str(event)
        parsed = await self.router.parse_intent(text)
        from uuid import uuid4
        canonical = {"intent_id":str(uuid4()),"channel":event.get("channel",0),**parsed}
        return await self._run_pipeline(canonical, None, parsed)

    async def _run_pipeline(self, canonical, fault, llm_out) -> dict:
        intent_id = canonical["intent_id"]
        sim_result = await self._simulate(canonical)
        has_conflict = False
        llm_conf   = float(llm_out.get("confidence",0.65))
        has_redund = fault.has_redundancy if fault else True
        breakdown  = score_intent(llm_confidence=llm_conf,
            sim_verdict=sim_result.get("verdict"),
            has_conflict=has_conflict, has_redundancy=has_redund,
            intent_priority=int(canonical.get("priority",50)),
            category=canonical.get("category","reachability"))
        fault_dict = _fault_to_dict(fault)
        result     = await self._route(canonical, breakdown, sim_result, fault_dict)
        entry = {"intent_id":intent_id,"intent":canonical,"simulation":sim_result,
                 "fault_analysis":fault_dict,"llm_analysis":llm_out,
                 "score":{"final_score":breakdown.final_score,"decision":breakdown.decision.value,
                          "reason":breakdown.reason,"recommendations":breakdown.recommendations},
                 "decision":breakdown.decision.value,"result":result,
                 "status":result.get("status","unknown"),
                 "created_at":datetime.utcnow().isoformat()}
        INTENT_STORE[intent_id] = entry
        return entry

    async def _simulate(self, canonical) -> dict:
        try:
            import httpx
            gateway = os.getenv("GATEWAY_URL","http://localhost:8000")
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{gateway}/api/simulate", json=canonical)
                return r.json()
        except Exception as e:
            return {"verdict":"WARN","checks":[],"detail":f"Simulation unavailable: {e}"}

    async def _route(self, canonical, breakdown, sim_result, fault_dict) -> dict:
        decision = breakdown.decision
        if decision == Decision.AUTO_DEPLOY:
            deploy = await self._deploy(canonical)
            return {"status":"deployed","decision":decision.value,
                    "score":breakdown.final_score,"reason":breakdown.reason,"deployment":deploy}
        elif decision == Decision.OPERATOR_APPROVAL:
            approval_req = build_approval_request(
                {"intent_id":canonical["intent_id"],"intent":canonical},
                breakdown, sim_result, fault_dict)
            PENDING_APPROVALS[canonical["intent_id"]] = approval_req.__dict__
            await self._notify_approval(approval_req)
            return {"status":"pending_approval","decision":decision.value,
                    "score":breakdown.final_score,"reason":breakdown.reason,
                    "approval_url":approval_req.approval_url,
                    "expires_at":approval_req.expires_at,
                    "recommendations":breakdown.recommendations}
        elif decision == Decision.REJECT:
            return {"status":"rejected","decision":decision.value,
                    "score":breakdown.final_score,"reason":breakdown.reason,
                    "recommendations":breakdown.recommendations,
                    "resubmit_hint":"Clarify intent, fix simulation issues, then resubmit"}
        else:
            return {"status":"feasibility_check","decision":decision.value,
                    "score":breakdown.final_score,"reason":breakdown.reason,
                    "recommendations":breakdown.recommendations,
                    "next_steps":["Review feasibility","Gather missing topology info",
                                  "Clarify intent with specific endpoints","Escalate to senior engineer"]}

    async def _deploy(self, canonical) -> dict:
        try:
            import sys
            sys.path.insert(0, os.getcwd())
            from automation.eapi_deployer import EAPIDeployer
            from models.intent_models import CanonicalIntent
            intent   = CanonicalIntent(**canonical)
            deployer = EAPIDeployer()
            result   = await deployer.deploy(intent)
            return result.dict()
        except Exception as e:
            log.warning(f"eAPI deploy failed: {e}")
            return {"status":"failed","detail":str(e)}


    async def _notify_approval(self, approval_req):
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN","")
        tg_chats = os.getenv("TELEGRAM_NOC_CHATS","").split(",")
        if tg_token and tg_chats and tg_chats[0]:
            try:
                import httpx
                for chat_id in tg_chats:
                    chat_id = chat_id.strip()
                    if not chat_id: continue
                    async with httpx.AsyncClient(timeout=10) as c:
                        await c.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                            json={"chat_id":chat_id,"text":approval_req.telegram_msg,"parse_mode":"Markdown"})
            except Exception as e:
                log.warning(f"Telegram notification failed: {e}")

async def operator_approve(intent_id: str, operator: str, method: str = "odl") -> dict:
    if intent_id not in PENDING_APPROVALS:
        return {"error":f"No pending approval for {intent_id}"}
    PENDING_APPROVALS.pop(intent_id)
    entry    = INTENT_STORE.get(intent_id,{})
    canonical= entry.get("intent",{})
    pipeline = IBNPipeline()
    deploy   = await pipeline._deploy(canonical)
    entry.update({"status":"deployed_after_approval","approved_by":operator,
                  "approved_at":datetime.utcnow().isoformat(),"deployment":deploy})
    INTENT_STORE[intent_id] = entry
    return {"status":"deployed","approved_by":operator,"deployment":deploy}

def _fault_to_dict(fault) -> dict:
    if not fault: return {}
    return {"affected_nodes":fault.affected_nodes,"affected_vlans":fault.affected_vlans,
            "affected_zones":fault.affected_zones,"affected_services":fault.affected_services,
            "has_redundancy":fault.has_redundancy,"alternate_paths":fault.alternate_paths,
            "impact_severity":fault.impact_severity,"confidence":fault.confidence,
            "remediation_steps":fault.remediation_steps}
