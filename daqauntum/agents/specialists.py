from __future__ import annotations

from .base import AgentResult, BaseAgent


class TeacherAgent(BaseAgent):
    name = "teacher"
    keywords = ("lesson", "student", "class", "worksheet", "quiz", "rubric", "teach", "curriculum")
    system_prompt = """You are DaQauntum's Teacher specialist. Design clear, rigorous, engaging learning experiences. Match explanations to the learner, separate objectives from activities, and favor evidence of understanding over busywork."""

    def run(self, request, context):
        return AgentResult(
            agent=self.name,
            summary="Build an instructional response that is usable by a real teacher and aligned to evidence of learning.",
            workflow=[
                "Identify the learning target and prerequisite knowledge",
                "Choose an instructional sequence appropriate to the learners",
                "Create practice or activity steps that reveal understanding",
                "Include an assessment or check for understanding",
            ],
            priorities=["student clarity", "engagement", "evidence of learning", "classroom usability"],
            constraints=["Avoid busywork", "Separate objectives, activities, and assessment"],
        )


class ResearchAgent(BaseAgent):
    name = "researcher"
    keywords = ("research", "source", "study", "paper", "evidence", "analyze", "literature", "citation")
    system_prompt = """You are DaQauntum's Research specialist. Separate sourced evidence from inference and speculation. Identify uncertainty, competing explanations, missing evidence, and the next test that would reduce uncertainty."""

    def run(self, request, context):
        return AgentResult(
            agent=self.name,
            summary="Treat the request as a research problem with explicit evidence, uncertainty, and provenance.",
            workflow=[
                "Define the research question precisely",
                "Identify what evidence is available and what is missing",
                "Compare plausible interpretations or competing explanations",
                "State confidence and propose the next evidence-gathering step",
            ],
            priorities=["source quality", "provenance", "uncertainty", "falsifiability"],
            constraints=["Do not present inference as sourced fact", "Flag missing evidence"],
        )


class ScientistAgent(BaseAgent):
    name = "scientist"
    keywords = ("physics", "experiment", "hypothesis", "data", "quantum", "force", "velocity", "acceleration")
    system_prompt = """You are DaQauntum's Scientist specialist. Use quantitative reasoning where appropriate, distinguish models from observations, define variables and assumptions, and propose falsifiable tests rather than overstating conclusions."""

    def run(self, request, context):
        return AgentResult(
            agent=self.name,
            summary="Frame the request as a scientific reasoning task and connect claims to measurements, models, or falsifiable tests.",
            workflow=[
                "Define the system, variables, assumptions, and measurable quantities",
                "Choose the relevant physical or mathematical model",
                "Distinguish observations from interpretation",
                "Check units, limiting cases, uncertainty, or alternative explanations",
                "Propose a test, calculation, or experiment that could falsify the conclusion",
            ],
            priorities=["quantitative reasoning", "measurement", "uncertainty", "falsifiability"],
            constraints=["Do not overstate conclusions", "State assumptions explicitly"],
        )


class CoderAgent(BaseAgent):
    name = "coder"
    keywords = ("code", "python", "bug", "api", "program", "github", "terminal", "software", "repository")
    system_prompt = """You are DaQauntum's Coding specialist. Prefer simple, testable, maintainable implementations. State assumptions, preserve security boundaries, validate inputs, and do not claim code was tested unless a test actually ran."""

    def run(self, request, context):
        return AgentResult(
            agent=self.name,
            summary="Treat the request as an engineering task with explicit interfaces, tests, and failure handling.",
            workflow=[
                "Define the expected behavior and interfaces",
                "Choose the smallest maintainable implementation",
                "Validate inputs and preserve security boundaries",
                "Test the important path and at least one failure path",
            ],
            priorities=["correctness", "maintainability", "testability", "security"],
            constraints=["Do not claim unrun tests passed", "Avoid unnecessary dependencies"],
        )
