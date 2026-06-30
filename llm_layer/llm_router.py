import os, json, re, logging
from typing import Optional

log = logging.getLogger("ibn.llm_router")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY","")
OLLAMA_HOST       = os.getenv("OLLAMA_HOST","http://localhost:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL","llama3")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL","claude-sonnet-4-20250514")
LLM_BACKEND_OVERRIDE = os.getenv("LLM_BACKEND","").lower()

IBN_SYSTEM_PROMPT = """You are an expert network intent compiler for an IBN system.
Convert any input into a canonical intent JSON with these fields:
{"category":"segmentation|security|reachability|qos|resiliency|mobility|telemetry|remediation|vxlan|compliance",
"action":"allow|deny|prioritize|rate_limit|redirect|quarantine|remediate",
"priority":50,"subject":{"endpoint_group":"IP/CIDR","vlan":null,"zone":null},
"target":{"endpoint_group":"IP/CIDR","vlan":null,"zone":null},
"constraints":{"ports":null,"bandwidth_mbps":null,"dscp":null},
"confidence":0.8,"reasoning":"one sentence","description":"one sentence"}
Return ONLY the JSON. No markdown."""

IBN_LINK_ANALYSIS_PROMPT = """You are an expert network fault analyst.
Given a link-down event return ONLY this JSON:
{"fault_type":"link_down|node_failure|flap|err_disabled|bgp_down",
"root_cause":"most likely cause","affected_vlans":[],"affected_zones":[],
"affected_services":[],"impact_severity":"critical|high|medium|low",
"remediation_intent":{"category":"resiliency","action":"redirect",
"subject":{"endpoint_group":"","zone":""},"target":{"endpoint_group":"","zone":""},"constraints":{}},
"confidence":0.8,"reasoning":"explanation","alternative_actions":[]}"""

async def _probe_claude() -> bool:
    if not ANTHROPIC_API_KEY: return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.anthropic.com/v1/models",
                headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"})
            return r.status_code in (200,401)
    except Exception: return False

async def _probe_ollama() -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            return r.status_code == 200
    except Exception: return False

async def _call_claude(system: str, user: str) -> str:
    import httpx
    headers = {"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"}
    body    = {"model":CLAUDE_MODEL,"max_tokens":1024,"system":system,"messages":[{"role":"user","content":user}]}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        r.raise_for_status()
        return r.json()["content"][0]["text"]

async def _call_ollama_http(system: str, user: str) -> str:
    import httpx
    body = {"model":OLLAMA_MODEL,"stream":False,
            "messages":[{"role":"system","content":system},{"role":"user","content":user}],
            "options":{"temperature":0.1}}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{OLLAMA_HOST}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]

def _rule_based(text: str) -> dict:
    t = text.lower()
    if any(k in t for k in ["isolat","segment","vlan"]): cat,act,prio="segmentation","deny",85
    elif any(k in t for k in ["block","deny","firewall"]): cat,act,prio="security","deny",90
    elif any(k in t for k in ["qos","dscp","voip","bandwidth"]): cat,act,prio="qos","prioritize",75
    elif any(k in t for k in ["failover","resilien","backup"]): cat,act,prio="resiliency","redirect",80
    else: cat,act,prio="reachability","allow",60
    ips = re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d+)?)\b", text)
    return {"category":cat,"action":act,"priority":prio,
            "subject":{"endpoint_group":ips[0] if ips else "any","vlan":None,"zone":None},
            "target":{"endpoint_group":ips[1] if len(ips)>1 else "any","vlan":None,"zone":None},
            "constraints":{"ports":None,"bandwidth_mbps":None,"dscp":None},
            "confidence":0.55,"reasoning":"Rule-based fallback","description":text[:150]}

class LLMRouter:
    def __init__(self):
        self._backend = None

    async def _ensure_backend(self):
        if self._backend: return
        if LLM_BACKEND_OVERRIDE in ("claude","ollama","rules"):
            self._backend = LLM_BACKEND_OVERRIDE; return
        if await _probe_claude():
            self._backend = "claude"; log.info("LLM: Claude API"); return
        if await _probe_ollama():
            self._backend = "ollama"; log.info("LLM: OLLAMA"); return
        self._backend = "rules"; log.info("LLM: rule-based fallback")

    async def _call(self, system: str, user: str) -> str:
        await self._ensure_backend()
        if self._backend == "claude":  return await _call_claude(system, user)
        if self._backend == "ollama":  return await _call_ollama_http(system, user)
        return json.dumps(_rule_based(user))

    async def parse_intent(self, text: str) -> dict:
        """Parse intent with RAG device resolution."""
        import re as _re

        # step 1: get RAG devices
        rag_devs = []
        try:
            if os.getenv("RAG_ENABLED","").lower() == "true":
                from rag_retriever import retrieve
                ctx = retrieve(text)
                rag_devs = ctx.get("devices", [])
        except Exception:
            pass

        # step 2: try OLLAMA with short focused prompt
        result = None
        try:
            await self._ensure_backend()
            if self._backend in ("claude","ollama"):
                short_system = (
                    "Return ONLY a JSON object with: category, action, priority, "
                    "subject (endpoint_group, vlan), target (endpoint_group, vlan), "
                    "constraints, confidence, reasoning, description. "
                    "For endpoint_group use actual IP addresses or \"any\". "
                    "Never write IP/CIDR as a value."
                )
                raw = await self._call(short_system, text)
                raw = _re.sub(r"```json|```", "", raw).strip()
                result = json.loads(raw)
        except Exception as e:
            log.warning(f"LLM parse failed: {e}")
            result = None

        # step 3: fallback to rule-based if LLM failed
        if result is None:
            result = _rule_based(text)

        # step 4: fix invalid actions
        act_map = {"block":"deny","permit":"allow","drop":"deny",
                   "restrict":"deny","isolate":"deny","forward":"allow"}
        act = str(result.get("action","allow")).lower()
        if act in act_map:
            result["action"] = act_map[act]

        # step 5: resolve hostname/placeholder endpoints via RAG
        def _needs_resolve(ep):
            if not ep: return True
            s = str(ep).strip().lower()
            if s in ("any","","none","null"): return False
            if not _re.match(r"^[\d./:]+$", s): return True
            return False

        src = result.get("subject", {}).get("endpoint_group", "")
        if _needs_resolve(src):
            if rag_devs and rag_devs[0][1]:
                result.setdefault("subject", {})["endpoint_group"] = rag_devs[0][1]
                if rag_devs[0][3]:
                    result["subject"]["vlan"] = int(rag_devs[0][3])
                log.info(f"RAG resolved src: {src} -> {rag_devs[0][1]} VLAN {rag_devs[0][3]}")
            else:
                result.setdefault("subject", {})["endpoint_group"] = "any"

        dst = result.get("target", {}).get("endpoint_group", "")
        if _needs_resolve(dst):
            if len(rag_devs) > 1 and rag_devs[1][1]:
                result.setdefault("target", {})["endpoint_group"] = rag_devs[1][1]
                if rag_devs[1][3]:
                    result["target"]["vlan"] = int(rag_devs[1][3])
                log.info(f"RAG resolved dst: {dst} -> {rag_devs[1][1]}")
            else:
                result.setdefault("target", {})["endpoint_group"] = "any"

        return result

    async def analyse_link_down(self, event: dict) -> dict:
        try:
            raw = await self._call(IBN_LINK_ANALYSIS_PROMPT, json.dumps(event))
            raw = re.sub(r"```json|```","",raw).strip()
            return json.loads(raw)
        except Exception as e:
            log.warning(f"Link analysis failed: {e}")
            node = event.get("source","unknown")
            return {"fault_type":"link_down","root_cause":f"Failure on {node}",
                    "affected_vlans":event.get("affected_vlans",[]),
                    "affected_zones":event.get("affected_zones",[]),
                    "affected_services":["reachability"],"impact_severity":"high",
                    "remediation_intent":{"category":"resiliency","action":"redirect",
                    "subject":{"endpoint_group":node,"zone":"fabric"},
                    "target":{"endpoint_group":"backup_path","zone":"fabric"},"constraints":{}},
                    "confidence":0.72,"reasoning":"Rule-based link-down handler",
                    "alternative_actions":["manual investigation"]}

    async def get_backend_status(self) -> dict:
        await self._ensure_backend()
        return {"claude_api":await _probe_claude(),"ollama":await _probe_ollama(),
                "active":self._backend,"claude_model":CLAUDE_MODEL,"ollama_model":OLLAMA_MODEL}

_router: Optional[LLMRouter] = None
def get_router() -> LLMRouter:
    global _router
    if _router is None: _router = LLMRouter()
    return _router
