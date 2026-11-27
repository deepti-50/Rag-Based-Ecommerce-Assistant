"""
Entry point for LangGraph Studio, referenced by ../../langgraph.json.

    langgraph dev      (from backend/, with Postgres + Ollama running and the index built)

Studio runs the same graph as the API. To try order lookups in Studio, add
{"user_id": "<a users.id>"} under the run's Configurable settings.
"""
from app.agent.graph import build_graph

graph = build_graph()
