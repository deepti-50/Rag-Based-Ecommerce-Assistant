"""
The assistant as a LangGraph graph.

        START
          |
          v
       [agent] --(LLM asked for tools?)-- yes --> [tools]
          ^                                          |
          +------------- tool results ---------------+
          |
          no (final answer)
          v
         END

- agent: the LLM decides whether to answer or call tools (search_products = retrieval).
- tools: runs the requested tools and appends their results to the messages.
- Loops until the LLM answers, capped by AGENT_MAX_STEPS (recursion_limit).

Conversation memory lives in Postgres (conversations/messages tables): the /chat
endpoint loads the history and passes it in, so no LangGraph checkpointer is needed.

See it drawn and running: `langgraph dev` (LangGraph Studio) -> see README.
"""
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.llm import get_llm
from app.agent.tools import TOOLS

SYSTEM_PROMPT = (
    "You are the friendly shopping assistant of an Indian online store. Keep answers short and clear.\n"
    "- For anything about products (what we sell, prices, features, colours, sizes, stock) ALWAYS call "
    "search_products first and answer ONLY from tool results. Never invent products, prices or stock.\n"
    "- Prices are in Indian rupees; write them like ₹1,499.\n"
    "- For the customer's orders or deliveries, call get_my_orders.\n"
    "- If the tools return nothing useful, say so and suggest a different search.\n"
    "- Politely decline questions unrelated to shopping in this store."
)


class State(TypedDict):
    # add_messages = reducer: nodes return NEW messages and they get appended
    messages: Annotated[list[AnyMessage], add_messages]


def build_graph(llm: BaseChatModel | None = None):
    """Compile the agent graph. `llm` is injectable so tests can use a fake model."""
    model = (llm or get_llm()).bind_tools(TOOLS)

    async def agent(state: State, config: RunnableConfig) -> dict:
        # passing `config` keeps callbacks (LangSmith tracing, Studio streaming) attached
        response = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]], config)
        return {"messages": [response]}

    builder = StateGraph(State)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)  # tool_calls -> "tools", else -> END
    builder.add_edge("tools", "agent")
    return builder.compile(name="shop_assistant")
