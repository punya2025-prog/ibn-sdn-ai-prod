from fastapi import APIRouter, HTTPException
from automation_pipeline.pipeline import (IBNPipeline, operator_approve,
                                           PENDING_APPROVALS, INTENT_STORE)

pipeline_router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
_pipeline = None

def get_pipeline() -> IBNPipeline:
    global _pipeline
    if _pipeline is None: _pipeline = IBNPipeline()
    return _pipeline

@pipeline_router.post("/event")
async def submit_event(body: dict):
    return await get_pipeline().process_event(body)

@pipeline_router.post("/analyse/link-down")
async def analyse_link_down(body: dict):
    src = body.get("source",""); dst = body.get("peer","")
    if not src or not dst: raise HTTPException(400,"source and peer required")
    pipeline = get_pipeline()
    fault    = pipeline.topology.analyse_fault(src, dst, "down")
    llm_out  = await pipeline.router.analyse_link_down({**body,
        "affected_vlans":fault.affected_vlans,"affected_zones":fault.affected_zones,
        "has_redundancy":fault.has_redundancy})
    return {"topology_analysis":{"affected_nodes":fault.affected_nodes,
        "affected_vlans":fault.affected_vlans,"affected_zones":fault.affected_zones,
        "has_redundancy":fault.has_redundancy,"impact_severity":fault.impact_severity,
        "confidence":fault.confidence,"remediation_steps":fault.remediation_steps},
        "llm_analysis":llm_out,"topology_summary":pipeline.topology.summary()}

@pipeline_router.get("/approvals")
async def list_approvals():
    return {"pending":len(PENDING_APPROVALS),"items":list(PENDING_APPROVALS.values())}

@pipeline_router.post("/approvals/{intent_id}/approve")
async def approve_intent(intent_id: str, body: dict = None):
    operator = (body or {}).get("operator","operator")
    method   = (body or {}).get("method","odl")
    return await operator_approve(intent_id, operator, method)

@pipeline_router.post("/approvals/{intent_id}/reject")
async def reject_intent(intent_id: str, body: dict = None):
    if intent_id not in PENDING_APPROVALS:
        raise HTTPException(404,f"No pending approval for {intent_id}")
    PENDING_APPROVALS.pop(intent_id)
    reason = (body or {}).get("reason","Rejected by operator")
    if intent_id in INTENT_STORE:
        INTENT_STORE[intent_id]["status"] = "rejected_by_operator"
        INTENT_STORE[intent_id]["rejection_reason"] = reason
    return {"status":"rejected","intent_id":intent_id,"reason":reason}

@pipeline_router.get("/topology/summary")
async def topology_summary():
    return get_pipeline().topology.summary()

@pipeline_router.get("/llm/status")
async def llm_status():
    return await get_pipeline().router.get_backend_status()
