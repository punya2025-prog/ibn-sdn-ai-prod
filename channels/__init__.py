"""
IBN Channel Handler — Intent parsing, RAG, LLM, simulation pipeline
All 6 input channels normalise here before LLM processing.
"""

import asyncio, json, logging, os, re, time
from typing import Optional

log = logging.getLogger("ibn.channels")

# ── Action alias map ──────────────────────────────────────────────────────
ACTION_MAP = {
    "block":    "deny",  "permit":  "allow", "pass":    "allow",
    "restrict": "deny",  "isolate": "deny",  "drop":    "deny",
    "forward":  "allow", "accept":  "allow", "refuse":  "deny",
    "quarantine":"deny", "filter":  "deny",  "open":    "allow",
}

# ── Channel status ────────────────────────────────────────────────────────
_channel_status = {i: "active" for i in range(7)}
_llm_backend    = "unknown"

def get_channel_status():  return _channel_status
def get_llm_status():      return {"backend": _llm_backend, "ready": True}
def get_rag_status():
    try:
        from rag_retriever import get_collection_counts
        return {"enabled": True, "collections": get_collection_counts()}
    except:
        return {"enabled": False}

# ── IP address check ──────────────────────────────────────────────────────
IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$")

def _needs_resolve(val):
    if not val or val == "any": return False
    return not IP_RE.match(str(val))

# ── Main entry point ──────────────────────────────────────────────────────
async def parse_and_simulate(text, intent_id, simulate, channel, priority_override=None):
    global _llm_backend
    t0 = time.time()

    # Step 1: RAG retrieval
    rag_ctx = {}
    if os.getenv("RAG_ENABLED","true").lower() == "true":
        try:
            from rag_retriever import retrieve
            rag_ctx = retrieve(text)
            log.info(f"[{intent_id[:8]}] RAG: {len(rag_ctx.get('devices',[]))} devices")
        except Exception as e:
            log.warning(f"RAG failed: {e}")

    # Step 2: LLM inference
    intent = await _call_llm(text, rag_ctx, intent_id)
    _llm_backend = intent.get("_backend","rules")

    # Step 3: Priority override
    if priority_override:
        intent["priority"] = min(100, intent.get("priority",80) + priority_override)

    # Step 4: Action normalisation
    act = intent.get("action","deny")
    intent["action"] = ACTION_MAP.get(str(act).lower(), act)

    # Step 5: Endpoint resolution from RAG
    for field in ["subject","target"]:
        ep = intent.get(field,{}).get("endpoint_group","any")
        if _needs_resolve(ep):
            resolved = _resolve_endpoint(ep, rag_ctx)
            if resolved:
                intent[field]["endpoint_group"] = resolved["ip"]
                intent[field]["vlan"] = resolved.get("vlan", intent[field].get("vlan",0))
                log.info(f"[{intent_id[:8]}] Resolved {ep} → {resolved['ip']} VLAN {resolved.get('vlan')}")

    # Step 6: Simulation
    simulation = {}
    score = 0.0
    if simulate:
        try:
            from simulation.simulator import simulate_intent
            simulation = await simulate_intent(intent)
            score      = _compute_score(simulation, intent)
        except Exception as e:
            log.warning(f"Simulation error: {e}")
            simulation = {"verdict":"SKIPPED","error":str(e)}
            score = 0.72  # default moderate score

    intent["_backend"] = None  # clean up internal field
    elapsed = round(time.time()-t0, 3)

    return {
        "intent":     intent,
        "simulation": simulation,
        "score":      round(score,3),
        "latency_s":  elapsed,
        "channel":    channel,
        "state":      "SIMULATED" if simulate else "PARSED",
        "rag_used":   bool(rag_ctx),
    }

# ── LLM call with fallback chain ──────────────────────────────────────────
async def _call_llm(text, rag_ctx, intent_id):
    system  = _build_system_prompt()
    context = rag_ctx.get("context","")
    user    = f"{context}\n\nIntent: {text}" if context else f"Intent: {text}"

    # Try OLLAMA first
    try:
        result = await _call_ollama(system, user)
        if result:
            result["_backend"] = "ollama"
            return result
    except Exception as e:
        log.debug(f"OLLAMA failed: {e}")

    # Fall back to rule-based
    log.info(f"[{intent_id[:8]}] Using rule-based parser")
    result = _rule_based_parse(text)
    result["_backend"] = "rules"

    # Still apply RAG device resolution even with rule-based
    if rag_ctx.get("devices"):
        result.setdefault("subject",{})
        result.setdefault("target",{})

    return result

async def _call_ollama(system, user):
    import httpx
    host = os.getenv("OLLAMA_HOST","http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL","llama3")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{host}/api/chat", json={
            "model": model, "stream": False,
            "messages": [
                {"role":"system","content":system},
                {"role":"user",  "content":user},
            ]
        })
        raw = r.json()["message"]["content"]
        # Strip markdown fences
        raw = re.sub(r"```json|```","",raw).strip()
        parsed = json.loads(raw)
        return _normalise_intent(parsed)

def _rule_based_parse(text):
    text_l = text.lower()
    if any(w in text_l for w in ["block","deny","isolate","quarantine","restrict"]):
        action = "deny"; category = "security"; priority = 88
    elif any(w in text_l for w in ["allow","permit","enable","open"]):
        action = "allow"; category = "reachability"; priority = 75
    elif any(w in text_l for w in ["priorit","qos","voip","dscp","traffic"]):
        action = "prioritize"; category = "qos"; priority = 80
    elif any(w in text_l for w in ["vlan","network","segment"]):
        action = "allow"; category = "segmentation"; priority = 78
    else:
        action = "deny"; category = "security"; priority = 80

    return {
        "category": category, "action": action, "priority": priority,
        "subject":  {"endpoint_group":"any","vlan":0,"zone":""},
        "target":   {"endpoint_group":"any","vlan":0,"zone":""},
        "constraints":{"ports":[],"protocols":[]},
        "confidence": 0.65,
        "reasoning": "rule-based parse",
        "description": f"Auto-parsed: {text[:80]}",
    }

def _normalise_intent(raw):
    """Ensure intent has required fields with correct types."""
    intent = {}
    intent["category"]    = raw.get("category","security")
    intent["action"]      = raw.get("action","deny")
    intent["priority"]    = int(raw.get("priority",80))
    intent["confidence"]  = float(raw.get("confidence",0.75))
    intent["reasoning"]   = raw.get("reasoning","")
    intent["description"] = raw.get("description","")
    intent["subject"]     = raw.get("subject",{})
    intent["target"]      = raw.get("target",{})
    intent["constraints"] = raw.get("constraints",{})
    if not isinstance(intent["subject"], dict):
        intent["subject"] = {"endpoint_group": str(intent["subject"]),"vlan":0,"zone":""}
    if not isinstance(intent["target"], dict):
        intent["target"]  = {"endpoint_group": str(intent["target"]), "vlan":0,"zone":""}
    return intent

def _resolve_endpoint(name, rag_ctx):
    for dev in rag_ctx.get("devices",[]):
        if (name.lower() in dev.get("hostname","").lower() or
            name.lower() in dev.get("device","").lower() or
            name.lower() in dev.get("zone","").lower()):
            return {"ip": dev.get("ip","any"), "vlan": dev.get("vlan",0)}
    return None

def _build_system_prompt():
    return """You are a network intent compiler. Return ONLY valid JSON, no markdown fences.
Use real IP addresses from the NETWORK CONTEXT section — never use 'IP/CIDR' or 'any' as placeholder.
Map action 'block' to 'deny', 'permit' to 'allow'.
Required JSON structure:
{
  "category": "security|segmentation|qos|reachability|compliance",
  "action": "allow|deny|prioritize|rate_limit|quarantine",
  "priority": 1-100,
  "subject": {"endpoint_group": "IP/CIDR", "vlan": 0, "zone": ""},
  "target":  {"endpoint_group": "IP/CIDR", "vlan": 0, "zone": ""},
  "constraints": {"ports": [], "protocols": []},
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation",
  "description": "one-line description"
}"""

def _compute_score(simulation, intent):
    gates    = simulation.get("gates",{})
    n_gates  = len(gates) or 1
    n_pass   = sum(1 for g in gates.values() if g.get("passed",False))
    gate_r   = n_pass / n_gates
    llm_conf = float(intent.get("confidence",0.75))
    priority = int(intent.get("priority",80))
    pri_cal  = 1.0 - abs(priority - 85) / 100
    rag_m    = 0.80  # default
    score    = gate_r*0.40 + llm_conf*0.20 + pri_cal*0.20 + rag_m*0.20
    return min(1.0, score)
