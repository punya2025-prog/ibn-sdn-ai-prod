"""
IBN RAG Retriever — ChromaDB with offline sentence-transformers
Queries 7 knowledge bases: intents, topology, ARP, MAC, VLANs, ports, policies
"""

import os, json, logging
log = logging.getLogger("ibn.rag")

os.environ.setdefault("TRANSFORMERS_OFFLINE","1")
os.environ.setdefault("HF_HUB_OFFLINE","1")

_embedder  = None
_chroma_db = None
_COLLS     = ["kb1_intents","kb2_topology","kb3_arp","kb4_mac",
               "kb5_vlans","kb6_ports","kb7_policies"]

def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        local = "models/embeddings/sentence-transformers_all-MiniLM-L6-v2"
        path  = local if os.path.exists(local) else "all-MiniLM-L6-v2"
        _embedder = SentenceTransformer(path)
        log.info(f"Embedder loaded from {path}")
    return _embedder

def _get_db():
    global _chroma_db
    if _chroma_db is None:
        import chromadb
        path = os.getenv("CHROMA_PATH","rag_db")
        _chroma_db = chromadb.PersistentClient(path=path)
        log.info(f"ChromaDB loaded from {path}")
    return _chroma_db

def _query(collection_name, embedding, n=3):
    try:
        db   = _get_db()
        coll = db.get_collection(collection_name)
        res  = coll.query(query_embeddings=[embedding], n_results=min(n,coll.count()))
        docs = res.get("documents",[[]])[0]
        mets = res.get("metadatas",[[]])[0]
        return [{"text":d,"meta":m} for d,m in zip(docs,mets)]
    except Exception as e:
        log.debug(f"RAG query {collection_name}: {e}")
        return []

def retrieve(text: str) -> dict:
    try:
        emb = _get_embedder().encode([text])[0].tolist()

        devices  = _query("kb3_arp", emb, n=5)
        macs     = _query("kb4_mac", emb, n=3)
        intents  = _query("kb1_intents", emb, n=3)
        vlans    = _query("kb5_vlans", emb, n=3)
        ports    = _query("kb6_ports", emb, n=3)
        policies = _query("kb7_policies", emb, n=2)
        topology = _query("kb2_topology", emb, n=3)

        # Build context string for LLM
        ctx_lines = []

        if devices:
            ctx_lines.append("DEVICE LOOKUP:")
            for d in devices[:3]:
                m = d.get("meta",{})
                if m.get("ip"):
                    ctx_lines.append(
                        f"  {m.get('hostname','?')} = "
                        f"IP:{m.get('ip')} MAC:{m.get('mac','')} "
                        f"VLAN:{m.get('vlan','')} zone:{m.get('zone','')} "
                        f"switch:{m.get('switch','')} port:{m.get('port','')}"
                    )

        if vlans:
            ctx_lines.append("VLAN CONTEXT:")
            for v in vlans[:2]:
                m = v.get("meta",{})
                ctx_lines.append(
                    f"  VLAN {m.get('vlan_id','')} = {m.get('name','')} "
                    f"zone:{m.get('zone','')} subnet:{m.get('subnet','')}"
                )

        if intents:
            ctx_lines.append("SIMILAR PAST INTENTS:")
            for i in intents[:2]:
                m = i.get("meta",{})
                ctx_lines.append(
                    f"  category:{m.get('category','')} "
                    f"action:{m.get('action','')} "
                    f"priority:{m.get('priority','')}"
                )

        if policies:
            ctx_lines.append("APPLICABLE POLICIES:")
            for pol in policies:
                ctx_lines.append(f"  {pol.get('text','')[:120]}")

        return {
            "available": True,
            "devices":   [d.get("meta",{}) for d in devices],
            "vlans":     [v.get("meta",{}) for v in vlans],
            "context":   "\n".join(ctx_lines),
        }
    except Exception as e:
        log.warning(f"RAG retrieve failed: {e}")
        return {"available":False,"devices":[],"vlans":[],"context":""}

def get_collection_counts():
    try:
        db = _get_db()
        return {c: db.get_collection(c).count() for c in _COLLS}
    except:
        return {}
