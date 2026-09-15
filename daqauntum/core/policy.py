from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskProfile:
    domain: str
    privacy: str
    complexity: int
    risk: str
    latency: str
    cost: str
    signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyDecision:
    enabled: bool
    role_hints: dict[str, dict[str, Any]]
    independent_critic: bool
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "role_hints": self.role_hints,
            "independent_critic": self.independent_critic,
            "reasons": self.reasons,
        }


class TaskProfiler:
    """Deterministic request profiler used before model selection.

    This is intentionally application code rather than an LLM judgment. The policy
    can therefore be inspected, tested, and changed without trusting model output.
    """

    HIGH_PRIVACY_PATTERNS = (
        r"\bpassword\b",
        r"\bpasscode\b",
        r"\bapi[ _-]?key\b",
        r"\bsecret key\b",
        r"\bprivate key\b",
        r"\bsocial security\b",
        r"\bssn\b",
        r"\bstudent records?\b",
        r"\bstudent grades?\b",
        r"\biep\b",
        r"\bmedical records?\b",
        r"\bhealth records?\b",
        r"\bcredentials?\b",
    )
    MEDIUM_PRIVACY_PATTERNS = (
        r"\bconfidential\b",
        r"\bprivate\b",
        r"\bpersonal data\b",
        r"\bpersonally identifiable\b",
        r"\bpii\b",
    )
    HIGH_RISK_PATTERNS = (
        r"\bdelete\b",
        r"\bpurchase\b",
        r"\bbuy\b",
        r"\bpublish\b",
        r"\bsend (?:an )?email\b",
        r"\bchange credentials?\b",
        r"\bexecute\b",
        r"\brun command\b",
    )
    COMPLEX_PATTERNS = (
        r"\banaly[sz]e\b",
        r"\bsynthesi[sz]e\b",
        r"\bresearch\b",
        r"\bderive\b",
        r"\bdebug\b",
        r"\barchitect\b",
        r"\bstatistical\b",
        r"\bsimulation\b",
        r"\brigor(?:ous|ously)\b",
        r"\bcompare\b",
        r"\bevaluate\b",
        r"\bdesign\b",
    )

    def profile(self, request: str, domain: str = "general") -> TaskProfile:
        text = request.lower()
        signals: list[str] = []

        privacy = "low"
        if self._matches(text, self.HIGH_PRIVACY_PATTERNS):
            privacy = "high"
            signals.append("sensitive-data signal")
        elif self._matches(text, self.MEDIUM_PRIVACY_PATTERNS):
            privacy = "medium"
            signals.append("privacy signal")

        risk = "low"
        if self._matches(text, self.HIGH_RISK_PATTERNS):
            risk = "high"
            signals.append("consequential-action signal")
        elif any(word in text for word in ("modify", "write", "save", "upload", "schedule")):
            risk = "medium"
            signals.append("state-change signal")

        complexity = 1
        complex_hits = sum(1 for pattern in self.COMPLEX_PATTERNS if re.search(pattern, text))
        if len(request) > 280:
            complexity += 1
            signals.append("long request")
        if complex_hits >= 1:
            complexity += 1
        if complex_hits >= 3:
            complexity += 1
            signals.append("multi-reasoning request")
        if request.count("\n") >= 3 or text.count(" and ") >= 3:
            complexity += 1
            signals.append("multi-step request")
        if any(term in text for term in ("deep research", "formal proof", "production", "architecture", "statistically significant")):
            complexity += 1
            signals.append("high-depth signal")
        complexity = min(5, complexity)

        latency = "normal"
        if any(term in text for term in ("quick", "quickly", "fast", "brief")):
            latency = "fast"
            signals.append("latency preference")
        elif any(term in text for term in ("deep", "thorough", "rigorous", "comprehensive")):
            latency = "quality"
            signals.append("quality preference")

        cost = "balanced"
        if any(term in text for term in ("cheap", "lowest cost", "save tokens", "economy", "local only")):
            cost = "economy"
            signals.append("cost preference")
        elif any(term in text for term in ("best model", "strongest model", "maximum quality", "highest quality")):
            cost = "performance"
            signals.append("performance preference")

        return TaskProfile(
            domain=domain,
            privacy=privacy,
            complexity=complexity,
            risk=risk,
            latency=latency,
            cost=cost,
            signals=signals,
        )

    @staticmethod
    def _matches(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


class ModelPolicy:
    """Maps a deterministic TaskProfile to model-router hints."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.local_only_for_sensitive = bool(cfg.get("local_only_for_sensitive", True))
        self.second_opinion_enabled = bool(cfg.get("second_opinion_enabled", True))
        self.second_opinion_complexity = int(cfg.get("second_opinion_complexity", 4))
        self.confidence_threshold = float(cfg.get("confidence_threshold", 0.65))
        self.hosted_providers = list(cfg.get("hosted_providers", ["openai", "anthropic"]))
        self.local_providers = list(cfg.get("local_providers", ["ollama", "mock"]))
        self.profiler = TaskProfiler()

    def analyze(
        self,
        request: str,
        domain: str,
        context: list[dict[str, Any]] | str | None = None,
    ) -> TaskProfile:
        profile = self.profiler.profile(request, domain)
        if context:
            if isinstance(context, str):
                context_text = context
            else:
                context_text = "\n".join(str(item.get("content", "")) for item in context[-8:])
            context_profile = self.profiler.profile(context_text, domain)
            levels = {"low": 0, "medium": 1, "high": 2}
            if levels.get(context_profile.privacy, 0) > levels.get(profile.privacy, 0):
                profile.privacy = context_profile.privacy
                profile.signals.append("sensitive recent context")
        return profile

    def decide(self, profile: TaskProfile) -> PolicyDecision:
        if not self.enabled:
            return PolicyDecision(False, {}, False, ["dynamic model policy disabled"])

        reasons: list[str] = []
        independent = False

        local = self._unique(self.local_providers + ["mock"])
        hosted = self._unique(self.hosted_providers)
        local_non_mock = [p for p in local if p != "mock"]
        local_last = local_non_mock + (["mock"] if "mock" in local else [])

        if profile.privacy == "high" and self.local_only_for_sensitive:
            reasons.append("high-privacy request restricted to local/offline providers")
            hints = {
                "planner": {"preference": local_last, "allow_hosted": False, "exclude_providers": hosted, "model_tier": "fast"},
                "executor": {"preference": local_last, "allow_hosted": False, "exclude_providers": hosted, "model_tier": "strong"},
                "critic": {"preference": local_last, "allow_hosted": False, "exclude_providers": hosted, "model_tier": "strong"},
            }
            independent = profile.risk == "high" or profile.complexity >= self.second_opinion_complexity
            return PolicyDecision(True, hints, independent, reasons)

        if profile.complexity >= 4 or profile.latency == "quality" or profile.cost == "performance":
            tier = "strong"
            reasons.append("complex/quality-sensitive request prefers stronger reasoning")
            planner_pref = self._unique(local_non_mock + hosted + ["mock"])
            executor_pref = self._unique(hosted + local_non_mock + ["mock"])
            critic_pref = self._unique(list(reversed(hosted)) + local_non_mock + ["mock"])
            independent = self.second_opinion_enabled
        elif profile.latency == "fast" or profile.cost == "economy" or profile.complexity <= 2:
            tier = "fast"
            reasons.append("simple/latency-sensitive request prefers lower-cost inference")
            planner_pref = self._unique(local_non_mock + hosted + ["mock"])
            executor_pref = self._unique(local_non_mock + hosted + ["mock"])
            critic_pref = self._unique(local_non_mock + list(reversed(hosted)) + ["mock"])
        else:
            tier = "balanced"
            reasons.append("balanced routing for moderate request")
            planner_pref = self._unique(local_non_mock + hosted + ["mock"])
            executor_pref = self._unique(hosted + local_non_mock + ["mock"])
            critic_pref = self._unique(local_non_mock + list(reversed(hosted)) + ["mock"])

        if profile.risk == "high":
            independent = self.second_opinion_enabled
            reasons.append("consequential action requests independent review")

        if profile.domain in {"scientist", "researcher", "coder"} and profile.complexity >= 3:
            reasons.append(f"{profile.domain} domain keeps hosted reasoning available")

        hints = {
            "planner": {"preference": planner_pref, "model_tier": "fast" if tier == "fast" else "balanced"},
            "executor": {"preference": executor_pref, "model_tier": tier},
            "critic": {"preference": critic_pref, "model_tier": "strong" if independent else tier},
        }
        return PolicyDecision(True, hints, independent, reasons)

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        result: list[str] = []
        for item in items:
            value = str(item).lower().strip()
            if value and value not in result:
                result.append(value)
        return result

    def should_secondary_review(self, profile: TaskProfile, confidence: float) -> bool:
        if not self.enabled or not self.second_opinion_enabled:
            return False
        return (
            confidence < self.confidence_threshold
            or profile.risk == "high"
            or profile.complexity >= self.second_opinion_complexity
        )


class ConfidenceEstimator:
    """Transparent heuristic confidence estimate for routing extra review.

    This is not a calibrated probability. It is a stable policy signal that makes
    second-opinion behavior testable and observable.
    """

    UNCERTAIN_PHRASES = (
        "i'm not sure",
        "i am not sure",
        "uncertain",
        "cannot verify",
        "can't verify",
        "may be",
        "might be",
        "unknown",
    )

    @classmethod
    def estimate(
        cls,
        draft: str,
        executor_fallback: bool,
        critic_approved: bool,
        critic_issues: list[str],
    ) -> float:
        score = 0.90
        if executor_fallback:
            score -= 0.15
        lowered = draft.lower()
        if any(phrase in lowered for phrase in cls.UNCERTAIN_PHRASES):
            score -= 0.15
        if not critic_approved:
            score -= 0.20
        score -= min(0.20, 0.04 * len(critic_issues))
        if len(draft.strip()) < 40:
            score -= 0.08
        return round(max(0.05, min(0.99, score)), 2)
