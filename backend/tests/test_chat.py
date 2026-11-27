"""
Assistant tests. The LLM is a scripted fake (no Ollama needed); everything else
is real: the LangGraph graph, the tools, Postgres, and a temporary Chroma index.
"""
import hashlib
import math
import uuid
from decimal import Decimal

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from app.agent import tools as agent_tools
from app.agent.graph import build_graph
from app.core.database import async_session
from app.main import app
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.user import User
from app.rag import indexer, vector_store
from app.routers.chat import get_agent
from tests.conftest import bearer
from tests.test_catalog import admin_headers, catalog, customer_headers  # noqa: F401  (fixtures)


# ---------------- fakes ----------------

class ScriptedLLM(BaseChatModel):
    """Returns the scripted AIMessages in order and records what it was sent."""
    script: list[AIMessage]
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def tool_call(name: str, **args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}])


class BagOfWordsEmbedding:
    """Tiny deterministic embedder so tests don't download the real model."""
    DIM = 256

    def __call__(self, input):
        out = []
        for text in input:
            v = [0.0] * self.DIM
            for word in text.lower().replace(".", " ").replace(",", " ").split():
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    def embed_query(self, input):
        return self(input)

    @staticmethod
    def name() -> str:
        return "bag_of_words_test"

    def is_legacy(self) -> bool:
        return False


@pytest.fixture
def use_agent():
    """Install a scripted LLM for the /chat endpoint; returns the fake for assertions."""
    def _use(*script: AIMessage) -> ScriptedLLM:
        llm = ScriptedLLM(script=list(script), calls=[])
        app.dependency_overrides[get_agent] = lambda: build_graph(llm)
        return llm
    yield _use
    app.dependency_overrides.pop(get_agent, None)


@pytest.fixture
async def product_index(tmp_path, monkeypatch, catalog):  # noqa: F811
    import chromadb

    client = chromadb.EphemeralClient()
    coll = client.get_or_create_collection(f"test_{uuid.uuid4().hex}", embedding_function=BagOfWordsEmbedding())
    monkeypatch.setattr(vector_store, "get_collection", lambda: coll)
    monkeypatch.setattr(indexer, "get_collection", lambda: coll)
    monkeypatch.setattr(agent_tools, "get_collection", lambda: coll)
    await indexer.index_products()
    return coll


async def _user_id(email: str) -> uuid.UUID:
    async with async_session() as db:
        return await db.scalar(select(User.id).where(User.email == email))


# ---------------- /chat endpoint ----------------

async def test_chat_requires_login(client):
    assert (await client.post("/chat", json={"message": "hi"})).status_code == 401


async def test_chat_answers_and_saves_history(client, tokens, use_agent):
    llm = use_agent(AIMessage("Hello! How can I help?"), AIMessage("We sell clothes and more."))
    h = bearer(tokens["access_token"])

    first = await client.post("/chat", json={"message": "hi"}, headers=h)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["answer"] == "Hello! How can I help?" and body["tools_used"] == []

    # Follow-up in the same conversation: the LLM must receive the earlier turn
    second = await client.post(
        "/chat", json={"message": "what do you sell?", "conversation_id": body["conversation_id"]}, headers=h
    )
    assert second.status_code == 200
    sent = [m.content for m in llm.calls[-1][1:]]  # [0] is the system prompt
    assert sent == ["hi", "Hello! How can I help?", "what do you sell?"]

    msgs = (await client.get(f"/chat/conversations/{body['conversation_id']}/messages", headers=h)).json()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hi"), ("assistant", "Hello! How can I help?"),
        ("user", "what do you sell?"), ("assistant", "We sell clothes and more."),
    ]
    convs = (await client.get("/chat/conversations", headers=h)).json()
    assert len(convs) == 1 and convs[0]["title"] == "hi"


async def test_cannot_use_someone_elses_conversation(client, tokens, register, login, use_agent):
    use_agent(AIMessage("hi alice"))
    alice = bearer(tokens["access_token"])
    conv_id = (await client.post("/chat", json={"message": "hi"}, headers=alice)).json()["conversation_id"]

    await register("bob@example.com")
    bob = bearer((await login("bob@example.com")).json()["access_token"])
    resp = await client.post("/chat", json={"message": "hi", "conversation_id": conv_id}, headers=bob)
    assert resp.status_code == 404
    assert (await client.get(f"/chat/conversations/{conv_id}/messages", headers=bob)).status_code == 404


async def test_agent_searches_catalog_then_answers(client, customer_headers, product_index, use_agent):  # noqa: F811
    llm = use_agent(
        tool_call("search_products", query="wireless earbuds noise cancelling", in_stock_only=True),
        AIMessage("Wireless Earbuds are ₹1,499 and in stock."),
    )
    resp = await client.post("/chat", json={"message": "any earbuds?"}, headers=customer_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tools_used"] == [{"name": "search_products",
                                   "args": {"query": "wireless earbuds noise cancelling", "in_stock_only": True}}]
    # The second LLM call saw the real tool result (grounding)
    tool_msg = llm.calls[-1][-1]
    assert tool_msg.type == "tool" and "Wireless Earbuds" in tool_msg.content and "1499.00" in tool_msg.content


async def test_llm_down_returns_503(client, tokens, use_agent):
    use_agent()  # empty script -> the fake raises, like an unreachable Ollama
    resp = await client.post("/chat", json={"message": "hi"}, headers=bearer(tokens["access_token"]))
    assert resp.status_code == 503


# ---------------- tools ----------------

async def test_search_uses_fresh_db_values_and_filters(product_index, catalog):  # noqa: F811
    # Index says in stock, then the product sells out: search must trust Postgres
    async with async_session() as db:
        p = await db.get(Product, uuid.UUID(catalog["earbuds"]["id"]))
        p.stock_quantity = 0
        await db.commit()

    found = await agent_tools.search_products.ainvoke({"query": "wireless earbuds"})
    earbuds = next(r for r in found if r["name"] == "Wireless Earbuds")
    assert earbuds["in_stock"] is False and earbuds["price_inr"] == "1499.00"

    in_stock = await agent_tools.search_products.ainvoke({"query": "wireless earbuds", "in_stock_only": True})
    assert all(r["name"] != "Wireless Earbuds" for r in in_stock if isinstance(in_stock, list))

    cheap = await agent_tools.search_products.ainvoke({"query": "running shoes", "max_price": 3000})
    assert cheap[0]["name"] == "Trail Running Shoes"                  # best match first
    assert all(Decimal(r["price_inr"]) <= 3000 for r in cheap)        # Leather Loafers (3999) filtered out


async def test_deactivated_products_are_never_returned(product_index):
    found = await agent_tools.search_products.ainvoke({"query": "discontinued headphones"})
    assert all(r["name"] != "Discontinued Headphones" for r in found)


async def test_orders_tool_only_shows_the_callers_orders(catalog, tokens, register):  # noqa: F811
    await register("bob@example.com")
    alice_id = await _user_id("alice@example.com")
    bob_id = await _user_id("bob@example.com")
    async with async_session() as db:
        order = Order(user_id=bob_id, status="shipped", total_amount=Decimal("2499.00"), shipping_address={})
        order.items.append(OrderItem(product_id=uuid.UUID(catalog["runner"]["id"]), quantity=1,
                                     unit_price=Decimal("2499.00"), total_price=Decimal("2499.00")))
        db.add(order)
        await db.commit()

    bob_view = await agent_tools.get_my_orders.ainvoke({}, config={"configurable": {"user_id": str(bob_id)}})
    assert bob_view[0]["status"] == "shipped" and bob_view[0]["items"][0]["name"] == "Trail Running Shoes"

    # Alice asks for Bob's order id directly: not found
    alice_view = await agent_tools.get_my_orders.ainvoke(
        {"order_id": str(order.id)}, config={"configurable": {"user_id": str(alice_id)}}
    )
    assert alice_view == "No order found."
    assert "not signed in" in await agent_tools.get_my_orders.ainvoke({}, config={})
