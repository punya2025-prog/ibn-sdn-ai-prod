import re, yaml, json
from abc     import ABC, abstractmethod
from typing  import Any

try:
    import ollama as _ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

NL_SYSTEM_PROMPT = """You are a network intent compiler for an IBN system.
Convert the input into a JSON canonical intent with EXACTLY these fields:
{"category":"segmentation|security|reachability|qos|resiliency|mobility|telemetry|remediation|vxlan|compliance",
"action":"allow|deny|prioritize|rate_limit|redirect|quarantine|remediate",
"priority":50,"subject":{"endpoint_group":"IP/CIDR","vlan":null,"zone":null},
"target":{"endpoint_group":"IP/CIDR","vlan":null,"zone":null},
"constraints":{"ports":null,"bandwidth_mbps":null,"dscp":null},
"confidence":0.8,"description":"one sentence"}
Return ONLY the JSON object. No markdown."""

def _call_ollama(text: str) -> dict:
    """Call OLLAMA then resolve endpoints via RAG."""
    import re as _re, logging
    _log = logging.getLogger("ibn.channels")

    # step 1: get RAG devices FIRST before anything else
    rag_devs = []
    try:
        from rag_retriever import retrieve
        ctx = retrieve(text)
        rag_devs = ctx.get("devices", [])
        _log.info(f"RAG devices: {len(rag_devs)} found for: {text[:40]}")
    except Exception as e:
        _log.warning(f"RAG retrieve failed: {e}")

    # step 2: try OLLAMA
    result = {}
    if OLLAMA_AVAILABLE:
        try:
            resp = _ollama.chat(
                model="llama3",
                messages=[
                    {"role":"system","content":NL_SYSTEM_PROMPT},
                    {"role":"user","content":text}
                ],
                options={"temperature":0.1})
            raw = re.sub(r"```json|```","",
                         resp["message"]["content"]).strip()
            result = json.loads(raw)
            _log.info(f"OLLAMA parsed: {result.get('category')} {result.get('action')}")
        except Exception as e:
            _log.warning(f"OLLAMA failed: {e} — using rule-based")
            result = {}

    # step 3: rule-based fallback
    if not result:
        result = _rule_based_parse(text)

    # step 4: fix invalid actions
    act_map = {"block":"deny","permit":"allow","drop":"deny",
               "restrict":"deny","isolate":"deny","forward":"allow"}
    act = str(result.get("action","allow")).lower()
    if act in act_map:
        result["action"] = act_map[act]

    # step 5: ALWAYS resolve endpoint via RAG if we have devices
    # runs regardless of whether OLLAMA succeeded or failed
    def _needs_resolve(ep):
        if not ep: return True
        s = str(ep).strip().lower()
        if s in ("any","","none","null"): return False
        # not an IP address — needs resolving
        return not _re.match(r"^[\d./:]+$", s)

    src = result.get("subject",{}).get("endpoint_group","")
    _log.info(f"Pre-RAG src: '{src}' needs_resolve:{_needs_resolve(src)}")
    if _needs_resolve(src):
        if rag_devs and rag_devs[0][1]:
            result.setdefault("subject",{})["endpoint_group"] = rag_devs[0][1]
            if rag_devs[0][3]:
                result["subject"]["vlan"] = int(rag_devs[0][3])
            _log.info(f"RAG resolved src: {src} -> {rag_devs[0][1]} VLAN {rag_devs[0][3]}")
        else:
            result.setdefault("subject",{})["endpoint_group"] = "any"
            _log.info(f"RAG no device found — src set to any")

    dst = result.get("target",{}).get("endpoint_group","")
    if _needs_resolve(dst):
        if len(rag_devs) > 1 and rag_devs[1][1]:
            result.setdefault("target",{})["endpoint_group"] = rag_devs[1][1]
            if rag_devs[1][3]:
                result["target"]["vlan"] = int(rag_devs[1][3])
            _log.info(f"RAG resolved dst: {dst} -> {rag_devs[1][1]}")
        else:
            result.setdefault("target",{})["endpoint_group"] = "any"

    return result


    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run())
                return future.result(timeout=120)
        return loop.run_until_complete(_run())
    except Exception as e:
        import logging
        logging.getLogger("ibn.channels").warning(f"_call_ollama failed: {e}")
        return {}

def _rule_based_parse(text: str) -> dict:
    t = text.lower()
    if any(k in t for k in ["isolat","segment","vlan","separate"]):
        cat,act,prio = "segmentation","deny",85
    elif any(k in t for k in ["block","firewall","acl","deny","security"]):
        cat,act,prio = "security","deny",90
    elif any(k in t for k in ["voip","qos","dscp","bandwidth","priorit"]):
        cat,act,prio = "qos","prioritize",75
    elif any(k in t for k in ["failover","redundan","backup","resilien"]):
        cat,act,prio = "resiliency","redirect",80
    elif any(k in t for k in ["vxlan","vni","overlay","evpn"]):
        cat,act,prio = "vxlan","allow",70
    elif any(k in t for k in ["pci","sox","hipaa","gdpr","complian"]):
        cat,act,prio = "compliance","deny",95
    elif any(k in t for k in ["err-disabl","quarantin"]):
        cat,act,prio = "security","quarantine",92
    elif any(k in t for k in ["link down","port down","remediat"]):
        cat,act,prio = "resiliency","remediate",88
    else:
        cat,act,prio = "reachability","allow",60
    ips    = re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d+)?)\b", text)
    vlan_m = re.search(r"\bvlan\s*(\d+)\b", t)
    return {"category":cat,"action":act,"priority":prio,
            "subject":{"endpoint_group":ips[0] if ips else "any","vlan":int(vlan_m.group(1)) if vlan_m else None,"zone":None},
            "target":{"endpoint_group":ips[1] if len(ips)>1 else "any","vlan":None,"zone":None},
            "constraints":{"ports":None,"bandwidth_mbps":None,"dscp":None},
            "confidence":0.55,"description":text[:200]}

VALID_CATEGORIES = ["segmentation","security","reachability","qos","resiliency",
                    "mobility","telemetry","remediation","vxlan","compliance"]
VALID_ACTIONS    = ["allow","deny","prioritize","rate_limit","redirect",
                    "quarantine","remediate"]
ACTION_ALIASES   = {"block":"deny","permit":"allow","drop":"deny","forward":"allow","mark":"prioritize"}

def _clean_val(val, valid, default):
    if not val: return default
    for v in str(val).replace("|",",").replace("/",",").split(","):
        v = v.strip().lower()
        if v in valid: return v
        if v in ACTION_ALIASES and ACTION_ALIASES[v] in valid: return ACTION_ALIASES[v]
    return default

def _build_intent(parsed: dict, channel: int, raw: str):
    from models.intent_models import CanonicalIntent, EndpointSpec, IntentConstraints
    category = _clean_val(parsed.get("category",""), VALID_CATEGORIES, "reachability")
    action   = _clean_val(parsed.get("action",""),   VALID_ACTIONS,    "allow")
    try:    priority = int(parsed.get("priority", 50))
    except: priority = 50
    if priority < 1 or priority > 100: priority = 50
    return CanonicalIntent(
        channel=channel,
        category=category,
        action=action,
        priority=priority,
        subject=EndpointSpec(**{k:v for k,v in parsed.get("subject",{}).items() if k in EndpointSpec.model_fields}),
        target=EndpointSpec(**{k:v for k,v in parsed.get("target",{}).items() if k in EndpointSpec.model_fields}),
        constraints=IntentConstraints(**{k:v for k,v in parsed.get("constraints",{}).items() if k in IntentConstraints.model_fields}),
        description=parsed.get("description",""),
        raw_input=raw[:500])

class BaseChannel(ABC):
    @abstractmethod
    async def process(self, raw: Any): ...

class PromptChannel(BaseChannel):
    async def process(self, raw: dict):
        text = raw.get("text","")
        return _build_intent(_call_ollama(text), 0, text)

class ServiceRequestChannel(BaseChannel):
    PRIORITY_MAP = {"critical":95,"high":80,"medium":60,"low":40}
    async def process(self, raw: dict):
        desc   = f"{raw.get('subject','')}\n{raw.get('description','')}"
        ticket = raw.get("ticket_id","UNKNOWN")
        prio   = self.PRIORITY_MAP.get(raw.get("priority","medium").lower(),60)
        parsed = _call_ollama(desc)
        parsed["priority"] = prio
        parsed["description"] = f"[{ticket}] {parsed.get('description','')}"
        intent = _build_intent(parsed, 1, desc)
        intent.metadata["ticket_id"] = ticket
        return intent

class EmailChannel(BaseChannel):
    _STRIP  = re.compile(r"(-{3,}|_{3,}|On .+ wrote:|From:.+Sent:.+)", re.DOTALL)
    _URGENT = re.compile(r"\bURGENT\b|\bCRITICAL\b|\bASAP\b", re.IGNORECASE)
    async def process(self, raw: dict):
        body    = self._STRIP.sub("", raw.get("body","")).strip()
        subject = raw.get("subject","")
        text    = f"{subject}\n{body}"
        parsed  = _call_ollama(text)
        if self._URGENT.search(text):
            parsed["priority"] = max(parsed.get("priority",50), 85)
        intent = _build_intent(parsed, 2, text)
        intent.metadata["from"] = raw.get("from","")
        return intent

class TopologyChannel(BaseChannel):
    async def process(self, raw: dict):
        from models.intent_models import CanonicalIntent, EndpointSpec
        topo  = yaml.safe_load(raw.get("yaml","{}")) or {}
        nodes = topo.get("topology",{}).get("nodes",{})
        intent = CanonicalIntent(channel=3,category="reachability",action="allow",priority=50,
            subject=EndpointSpec(endpoint_group="topology_upload"),
            target=EndpointSpec(endpoint_group="fabric"),
            description=f"Topology: {raw.get('filename','unknown')} — {len(nodes)} nodes",
            raw_input=raw.get("yaml","")[:300])
        intent.metadata["node_count"] = len(nodes)
        return intent

class TelegramChannel(BaseChannel):
    _CMD = re.compile(r"^/(\w+)\s*(.*)", re.DOTALL)
    async def process(self, raw: dict):
        msg  = raw.get("message",{})
        text = msg.get("text","")
        chat = msg.get("chat",{}).get("id",0)
        user = msg.get("from",{}).get("username","unknown")
        m    = self._CMD.match(text)
        cmd, args = (m.group(1), m.group(2).strip()) if m else ("intent", text)
        intent = _build_intent(_call_ollama(args or text), 4, text)
        intent.metadata["telegram_chat_id"] = chat
        intent.metadata["telegram_user"]    = user
        return intent

class SMSChannel(BaseChannel):
    _KEYWORDS = {
        r"^BLOCK\s+(\S+)":         ("security",     "deny",      80),
        r"^ALLOW\s+(\S+)\s+(\S+)": ("reachability", "allow",     60),
        r"^QOS\s+(\S+)\s+(\S+)":   ("qos",          "prioritize",75),
        r"^ISOLATE\s+VLAN(\d+)":   ("segmentation", "deny",      85),
        r"^FAILOVER\s+(\S+)":      ("resiliency",   "redirect",  75),
    }
    async def process(self, raw: dict):
        from models.intent_models import CanonicalIntent, EndpointSpec
        text = (raw.get("Body") or raw.get("body") or raw.get("message","")).strip()
        src  = raw.get("From") or raw.get("from","unknown")
        for pattern, (cat, action, prio) in self._KEYWORDS.items():
            m = re.match(pattern, text, re.IGNORECASE)
            if m:
                groups = m.groups()
                intent = CanonicalIntent(channel=5,category=cat,action=action,priority=prio,
                    subject=EndpointSpec(endpoint_group=groups[0] if groups else "any"),
                    target=EndpointSpec(endpoint_group=groups[1] if len(groups)>1 else "any"),
                    description=f"SMS: {text[:100]}",raw_input=text)
                intent.metadata["sms_from"] = src
                return intent
        intent = _build_intent(_call_ollama(text), 5, text)
        intent.metadata["sms_from"] = src
        return intent

__all__ = ["PromptChannel","ServiceRequestChannel","EmailChannel",
           "TopologyChannel","TelegramChannel","SMSChannel"]
