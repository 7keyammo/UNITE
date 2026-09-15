from agents.specialists import TeacherAgent, ResearchAgent, ScientistAgent, CoderAgent


class AgentRouter:
    def __init__(self):
        self.agents = [TeacherAgent(), ResearchAgent(), ScientistAgent(), CoderAgent()]

    def choose(self, request: str):
        ranked = sorted(self.agents, key=lambda a: a.score(request), reverse=True)
        return ranked[0] if ranked and ranked[0].score(request) > 0 else None
