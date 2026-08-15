"""Job recommender: find periodic idle-heavy workloads and recommend a target
(Job/CronJob/KEDA/Knative) with cost savings and dependency-aware peers.
"""

from .builder import analyze_workload, build_recommendation
from .runner import RunResult, run_job_analysis
from .types import Peer, WorkloadRecommendation

__all__ = [
    "RunResult",
    "run_job_analysis",
    "analyze_workload",
    "build_recommendation",
    "Peer",
    "WorkloadRecommendation",
]
