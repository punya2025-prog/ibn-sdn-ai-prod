from pydantic import BaseModel, Field
from typing   import Optional
from enum     import IntEnum, Enum
from uuid     import uuid4
from datetime import datetime

class ChannelType(IntEnum):
    PROMPT=0; SERVICE_REQUEST=1; EMAIL=2; TOPOLOGY=3; TELEGRAM=4; SMS=5

class IntentAction(str, Enum):
    ALLOW="allow"; DENY="deny"; PRIORITIZE="prioritize"
    RATE_LIMIT="rate_limit"; REDIRECT="redirect"
    QUARANTINE="quarantine"; REMEDIATE="remediate"

class IntentCategory(str, Enum):
    SEGMENTATION="segmentation"; SECURITY="security"
    REACHABILITY="reachability"; QOS="qos"; RESILIENCY="resiliency"
    MOBILITY="mobility"; TELEMETRY="telemetry"; REMEDIATION="remediation"
    VXLAN="vxlan"; COMPLIANCE="compliance"

class EndpointSpec(BaseModel):
    endpoint_group: str       = "0.0.0.0/0"
    vlan:           Optional[int] = None
    zone:           Optional[str] = None
    device:         Optional[str] = None

class IntentConstraints(BaseModel):
    ports:          Optional[list[str]] = None
    bandwidth_mbps: Optional[float]     = None
    latency_ms:     Optional[float]     = None
    dscp:           Optional[str]       = None
    vni:            Optional[int]       = None
    vrf:            Optional[str]       = None
    time_window:    Optional[str]       = None
    protocols:      Optional[list[str]] = None
    extra:          Optional[dict]      = None

class CanonicalIntent(BaseModel):
    intent_id:   str               = Field(default_factory=lambda: str(uuid4()))
    channel:     int               = 0
    category:    IntentCategory    = IntentCategory.REACHABILITY
    action:      IntentAction      = IntentAction.ALLOW
    priority:    int               = Field(50, ge=1, le=100)
    subject:     EndpointSpec      = Field(default_factory=EndpointSpec)
    target:      EndpointSpec      = Field(default_factory=EndpointSpec)
    constraints: IntentConstraints = Field(default_factory=IntentConstraints)
    description: str               = ""
    raw_input:   Optional[str]     = None
    created_at:  str               = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata:    dict              = Field(default_factory=dict)

class ChannelInput(BaseModel):
    channel:  int  = 0
    payload:  dict = Field(default_factory=dict)
    simulate: bool = True
    deploy:   bool = False

class SimulationCheck(BaseModel):
    check_name: str
    passed:     bool
    detail:     str
    tool:       str

class SimulationResult(BaseModel):
    intent_id:        str
    verdict:          str
    checks:           list[SimulationCheck] = []
    duration_ms:      int                   = 0
    batfish_snapshot: Optional[str]         = None
    diff_preview:     Optional[str]         = None
    timestamp:        str = Field(default_factory=lambda: datetime.utcnow().isoformat())

class DeploymentResult(BaseModel):
    intent_id:     str
    method:        str
    nodes_updated: list[str] = []
    flows_pushed:  int       = 0
    status:        str       = "pending"
    detail:        str       = ""
    timestamp:     str = Field(default_factory=lambda: datetime.utcnow().isoformat())

class IntentStatus(BaseModel):
    intent_id:  str
    status:     str
    created_at: str
    intent:     Optional[CanonicalIntent]  = None
    simulation: Optional[SimulationResult] = None
    deployment: Optional[DeploymentResult] = None
