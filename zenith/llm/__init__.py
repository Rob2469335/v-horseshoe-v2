"""
llm/__init__.py - LLM Package
"""
from .router import ModelRouter


def create_router():
    return ModelRouter()


def get_model(task: str) -> str:
    router = create_router()
    return router.route_smart(task)["primary"]
