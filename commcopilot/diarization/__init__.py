"""Text-based speaker diarization for CommCopilot.

Implements a streaming variant of the Multiple Prediction Model (MPM) from
Wu & Choi, 2025 ("Do We Still Need Audio?"). Each transcript chunk enters a
sliding window; the DiarizationAgent labels every sentence in the current
window per call, and overlapping predictions are aggregated via majority vote
when a chunk exits the window.
"""

from commcopilot.diarization.aggregator import SlidingWindowAggregator
from commcopilot.diarization.types import LabeledChunk

__all__ = ["LabeledChunk", "SlidingWindowAggregator"]
