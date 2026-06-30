"""
IBN-SDN-AI Version 2 — FastAPI Gateway
Intent-Driven Business Network Modelling Using SDN and AI
Port: 8001  RAG: enabled  VLANs: 19  Devices: 192
"""

import asyncio, json, logging, os, uuid
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
log = logging.getLogger("ibn.gateway")

app = FastAPI(
    title="IBN-SDN-AI Gateway v2",
    description="Intent-Driven Business Network Modelling Using SDN and AI",
    version="2.0.0"
)

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── In-memory intent store ────────────────────────────────────────────────
intent_store = []
MAX_INTENTS  = 500

# ── Request/Response models ───────────────────────────────────────────────
class PromptRequest(BaseModel):
    text: str
    simulate: bool = True
    priority_override: Optional[int] = None
    channel: int = 0

class EventRequest(BaseModel):
    type: str
    source: str
    message: str
    severity: str = "info"
    metadata: dict = {}

class DeployRequest(BaseModel):
    intent_id: str
    force: bool = False

# ── Health check ──────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    from channels import get_channel_status
    from rag_retriever import get_rag_status
    return {
        "status": "healthy",
        "version": "2.0.0",
        "port": int(os.getenv("PORT", 8001)),
        "rag_enabled": os.getenv("RAG_ENABLED","true").lower()=="true",
        "channels_active": get_channel_status(),
        "rag_status": get_rag_status(),
        "intents_stored": len(intent_store),
        "timestamp": datetime.utcnow().isoformat()
    }

# ── Channel 0: Direct prompt ──────────────────────────────────────────────
@app.post("/api/channel/0/prompt")
async def channel_prompt(req: PromptRequest, bg: BackgroundTasks):
    return await _process_intent(req.text, req.simulate,
                                  req.channel, bg, req.priority_override)

# ── Channel 2: Email ──────────────────────────────────────────────────────
@app.post("/api/channel/2/email")
async def channel_email(req: PromptRequest, bg: BackgroundTasks):
    priority_boost = 0
    if "URGENT"   in req.text.upper(): priority_boost = 35
    if "CRITICAL" in req.text.upper(): priority_boost = 45
    return await _process_intent(req.text, req.simulate,
                                  2, bg, priority_boost or None)

# ── Channel 6: Syslog/Event ───────────────────────────────────────────────
@app.post("/api/events/ingest")
async def ingest_event(req: EventRequest, bg: BackgroundTasks):
    log.info(f"Event from {req.source}: {req.type} — {req.message}")
    auto_intents = ["link_down","port_err","bgp_drop","arp_flood","rogue_ap"]
    if req.type in auto_intents:
        text = f"auto remediate {req.type} on {req.source}: {req.message}"
        return await _process_intent(text, True, 6, bg)
    return {"status":"logged","event_id":str(uuid.uuid4())}

# ── Core intent processing ────────────────────────────────────────────────
async def _process_intent(text, simulate, channel, bg, priority_override=None):
    intent_id = str(uuid.uuid4())
    log.info(f"[{intent_id[:8]}] Processing: {text[:80]}")

    try:
        from channels import parse_and_simulate
        result = await parse_and_simulate(
            text, intent_id, simulate, channel, priority_override)

        # Store intent
        record = {
            "intent_id": intent_id,
            "timestamp": datetime.utcnow().isoformat(),
            "channel":   channel,
            "raw_input": text,
            "state":     result.get("state","SIMULATED"),
            "score":     result.get("score", 0),
            "result":    result
        }
        intent_store.insert(0, record)
        if len(intent_store) > MAX_INTENTS:
            intent_store.pop()

        # Auto-deploy if score high enough
        score = result.get("score", 0)
        if score >= 0.90 and simulate:
            bg.add_task(_auto_deploy, intent_id, result)
            result["deployment"] = "auto-deploying"
        elif score >= 0.70:
            result["deployment"] = "pending-approval"
        else:
            result["deployment"] = "rejected"

        return {"intent_id": intent_id, **result}

    except Exception as e:
        log.error(f"[{intent_id[:8]}] Error: {e}")
        raise HTTPException(500, str(e))

async def _auto_deploy(intent_id, result):
    await asyncio.sleep(1)
    try:
        from automation.eapi_deployer import deploy_intent
        intent = result.get("intent",{})
        deploy_result = await deploy_intent(intent)
        log.info(f"[{intent_id[:8]}] Auto-deployed: {deploy_result}")
        _update_store(intent_id, "DEPLOYED", deploy_result)
    except Exception as e:
        log.error(f"[{intent_id[:8]}] Deploy failed: {e}")
        _update_store(intent_id, "DEPLOY_FAILED", {"error": str(e)})

def _update_store(intent_id, state, extra):
    for r in intent_store:
        if r["intent_id"] == intent_id:
            r["state"] = state
            r["deploy_result"] = extra
            break

# ── Intent management ─────────────────────────────────────────────────────
@app.get("/api/intents")
async def list_intents(limit: int = 50, state: Optional[str] = None):
    data = intent_store[:limit]
    if state:
        data = [i for i in data if i.get("state","") == state]
    return {"intents": data, "total": len(intent_store)}

@app.get("/api/intents/{intent_id}")
async def get_intent(intent_id: str):
    for r in intent_store:
        if r["intent_id"] == intent_id:
            return r
    raise HTTPException(404, "Intent not found")

@app.post("/api/eapi/deploy/{intent_id}")
async def manual_deploy(intent_id: str, bg: BackgroundTasks):
    for r in intent_store:
        if r["intent_id"] == intent_id:
            bg.add_task(_auto_deploy, intent_id, r["result"])
            return {"status":"deploying","intent_id":intent_id}
    raise HTTPException(404, "Intent not found")

@app.post("/api/simulate")
async def simulate_only(req: PromptRequest):
    return await _process_intent(req.text, True, req.channel, BackgroundTasks())

@app.get("/api/pipeline/llm/status")
async def llm_status():
    from channels import get_llm_status
    return get_llm_status()

@app.get("/api/topology")
async def get_topology():
    try:
        with open("network_state/digital_twin_topology.json") as f:
            return json.load(f)
    except:
        return {"nodes":[],"links":[]}

@app.get("/api/network-state")
async def get_network_state():
    try:
        with open("network_state/network_state.json") as f:
            data = json.load(f)
            return {
                "devices": len(data.get("arp_table",[])),
                "vlans":   len(data.get("vlans",{})),
                "switches":len(data.get("switches",{})),
            }
    except:
        return {"devices":0,"vlans":0,"switches":0}

# ── GNN status ────────────────────────────────────────────────────────────
gnn_results = []

@app.get("/api/gnn/status")
async def gnn_status():
    return {"results": gnn_results[-13:], "count": len(gnn_results)}

async def gnn_loop():
    if os.getenv("GNN_ENABLED","true").lower() != "true":
        return
    interval = int(os.getenv("GNN_INTERVAL","60"))
    while True:
        try:
            from gnn_telemetry_fix import collect, SWITCHES
            from gnn_predictor_fixed import predict_faults
            telemetry = {h: collect(h,i) for h,i in SWITCHES.items()}
            results = predict_faults(telemetry)
            gnn_results.clear()
            gnn_results.extend(results)
            high = [r for r in results if r["fault_prob"] > 0.70]
            if high:
                for r in high:
                    log.warning(f"GNN HIGH RISK: {r['node']} "
                                f"prob={r['fault_prob']:.3f}")
        except Exception as e:
            log.debug(f"GNN loop: {e}")
        await asyncio.sleep(interval)

@app.on_event("startup")
async def startup():
    log.info("IBN Gateway v2 starting up...")
    asyncio.create_task(gnn_loop())
    log.info(f"Gateway ready on port {os.getenv('PORT',8001)}")

# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port,
                reload=False, log_level="info")
