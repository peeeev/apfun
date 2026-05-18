"""ORM models. Importing this package registers every table on `Base.metadata`."""

from apfun.models.approval import Approval, ApprovalDecision
from apfun.models.base import Base, IdMixin, TimestampMixin
from apfun.models.candidate import Candidate, CandidateSignal, Decision, PipelineStage
from apfun.models.competitive_analysis import CompetitiveAnalysis
from apfun.models.demand_check import DemandCheck, DemandVerdict
from apfun.models.opportunity import Opportunity, OpportunityStatus
from apfun.models.project import Project, ProjectStatus
from apfun.models.raw_signal import RawSignal
from apfun.models.score import Score
from apfun.models.source import Source

__all__ = [
    "Approval",
    "ApprovalDecision",
    "Base",
    "Candidate",
    "CandidateSignal",
    "CompetitiveAnalysis",
    "Decision",
    "DemandCheck",
    "DemandVerdict",
    "IdMixin",
    "Opportunity",
    "OpportunityStatus",
    "PipelineStage",
    "Project",
    "ProjectStatus",
    "RawSignal",
    "Score",
    "Source",
    "TimestampMixin",
]
