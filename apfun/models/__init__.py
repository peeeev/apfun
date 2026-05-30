"""ORM models. Importing this package registers every table on `Base.metadata`."""

from apfun.models.api_usage import ApiUsage
from apfun.models.approval import Approval, ApprovalDecision
from apfun.models.base import Base, IdMixin, TimestampMixin
from apfun.models.candidate import (
    Buildability,
    Candidate,
    CandidateSignal,
    Decision,
    PipelineStage,
)
from apfun.models.competitive_analysis import CompetitiveAnalysis
from apfun.models.dataforseo_usage import DataForSEOUsage
from apfun.models.demand_check import DemandCheck, DemandVerdict
from apfun.models.llm_run import LLMRun
from apfun.models.opportunity import Opportunity, OpportunityStatus
from apfun.models.project import Project, ProjectStatus
from apfun.models.raw_signal import RawSignal
from apfun.models.runtime_state import SCHEDULER_PAUSED_KEY, RuntimeState
from apfun.models.scheduler_run import SchedulerRun
from apfun.models.score import Score
from apfun.models.signal_text import SignalText
from apfun.models.source import Source

__all__ = [
    "ApiUsage",
    "Approval",
    "ApprovalDecision",
    "Base",
    "Buildability",
    "Candidate",
    "CandidateSignal",
    "CompetitiveAnalysis",
    "DataForSEOUsage",
    "Decision",
    "DemandCheck",
    "DemandVerdict",
    "IdMixin",
    "LLMRun",
    "Opportunity",
    "OpportunityStatus",
    "PipelineStage",
    "Project",
    "ProjectStatus",
    "SCHEDULER_PAUSED_KEY",
    "RawSignal",
    "RuntimeState",
    "SchedulerRun",
    "Score",
    "SignalText",
    "Source",
    "TimestampMixin",
]
