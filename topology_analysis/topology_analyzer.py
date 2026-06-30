import json, os, logging
from dataclasses import dataclass, field
from typing      import Optional
from pathlib     import Path

log = logging.getLogger("ibn.topology")
TOPOLOGY_FILE = os.getenv("TOPOLOGY_FILE","topology/digital_twin_topology.json")
STATE_FILE    = os.getenv("STATE_FILE","network_state/network_state.json")

@dataclass
class LinkState:
    src: str; src_intf: str; dst: str; dst_intf: str
    status: str = "up"; speed: str = "1G"

@dataclass
class FaultAnalysis:
    link:              LinkState
    affected_nodes:    list = field(default_factory=list)
    affected_vlans:    list = field(default_factory=list)
    affected_zones:    list = field(default_factory=list)
    affected_services: list = field(default_factory=list)
    has_redundancy:    bool = False
    alternate_paths:   list = field(default_factory=list)
    impact_severity:   str  = "medium"
    confidence:        float= 0.7
    remediation_steps: list = field(default_factory=list)

class TopologyAnalyzer:
    def __init__(self):
        self._loaded = False
        try:
            import networkx as nx
            self.graph = nx.Graph()
            self.NX    = nx
        except ImportError:
            self.graph = None
            self.NX    = None
        self.nodes = {}
        self.links = {}

    def load(self, topo_path=TOPOLOGY_FILE, state_path=STATE_FILE):
        if self.NX is None:
            log.warning("networkx not installed — topology analysis limited")
            self._loaded = True; return
        if Path(topo_path).exists():
            with open(topo_path) as f: topo = json.load(f)
            for node in topo.get("nodes",[]):
                nid = node["node_id"]
                self.nodes[nid] = node
                self.graph.add_node(nid, role=node.get("role",""),
                    fabric=node.get("fabric",""), vlans=node.get("vlans",[]))
            for link in topo.get("links",[]):
                src, dst = link["src"], link["dst"]
                self.graph.add_edge(src, dst, status="up",
                    speed=link.get("speed","1G"))
                ls = LinkState(src=src,src_intf=link.get("src_intf",""),
                               dst=dst,dst_intf=link.get("dst_intf",""))
                self.links[(src,dst)] = ls
                self.links[(dst,src)] = ls
        self._loaded = True
        log.info(f"Topology: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")

    def analyse_fault(self, src: str, dst: str, status: str = "down") -> FaultAnalysis:
        if not self._loaded: self.load()
        link = self.links.get((src,dst), LinkState(src=src,src_intf="",dst=dst,dst_intf="",status=status))
        link.status = status
        if self.NX is None or not self.graph.has_edge(src,dst):
            return FaultAnalysis(link=link, impact_severity="high",
                remediation_steps=[f"Check physical link {src} ↔ {dst}"])
        tmp = self.graph.copy()
        tmp.remove_edge(src, dst)
        has_redundancy = self.NX.has_path(tmp, src, dst) if self.NX.has_path(tmp, src, dst) else False
        affected_vlans = list(set(v for n in [src,dst]
            for v in self.graph.nodes[n].get("vlans",[])))
        affected_zones = list(set(self.graph.nodes[n].get("role","")
            for n in [src,dst] if self.graph.nodes[n].get("role")))
        severity = "critical" if any(z in ("scada","billing","voip") for z in affected_zones) \
                   else "high" if any(z in ("prod","finance") for z in affected_zones) else "medium"
        steps = ["ECMP failover available" if has_redundancy else "No redundant path — physical repair needed",
                 f"Notify affected services on VLANs {affected_vlans}"]
        return FaultAnalysis(link=link, affected_vlans=affected_vlans,
            affected_zones=affected_zones, has_redundancy=has_redundancy,
            impact_severity=severity, confidence=0.90 if has_redundancy else 0.72,
            remediation_steps=steps)

    def summary(self) -> dict:
        if not self._loaded: self.load()
        if self.NX is None:
            return {"nodes":len(self.nodes),"edges":0,"is_connected":False}
        return {"nodes":self.graph.number_of_nodes(),
                "edges":self.graph.number_of_edges(),
                "is_connected":self.NX.is_connected(self.graph) if self.graph.number_of_nodes()>0 else False,
                "links_down":[(s,d) for (s,d),lk in self.links.items() if lk.status!="up"]}
