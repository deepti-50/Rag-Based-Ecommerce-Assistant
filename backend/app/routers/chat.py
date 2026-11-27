"""
Chat with the shopping assistant (the LangGraph agent in app/agent/graph.py).

Each turn: load this conversation's history from Postgres -> run the graph ->
save the user message and the final answer.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import build_graph
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.security import TokenData
from app.models.conversation import Conversation, Message
from app.schemas.chat import ChatRequest, ChatResponse, ConversationOut, MessageOut, ToolCall

router = APIRouter(prefix="/chat", tags=["Assistant"])
logger = logging.getLogger("app.chat")

FALLBACK_ANSWER = "Sorry, I couldn't work that out. Could you rephrase or be more specific?"


@lru_cache
def get_agent():
    """Dependency: the compiled graph (built once). Tests override this with a fake LLM."""
    return build_graph()


async def _own_conversation(db: AsyncSession, conversation_id: uuid.UUID, user_id: str) -> Conversation:
    conv = await db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == uuid.UUID(user_id))
    )
    if not conv:  # 404 (not 403) so ids of other users' chats aren't confirmed to exist
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


async def _history(db: AsyncSession, conversation_id: uuid.UUID) -> list[BaseMessage]:
    rows = (await db.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(settings.CHAT_HISTORY_LIMIT)
    )).all()
    return [HumanMessage(m.content) if m.role == "user" else AIMessage(m.content) for m in reversed(rows)]


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    agent=Depends(get_agent),
):
    if body.conversation_id:
        conv = await _own_conversation(db, body.conversation_id, current_user.user_id)
        history = await _history(db, conv.id)
    else:
        conv = Conversation(user_id=uuid.UUID(current_user.user_id), title=body.message[:60])
        db.add(conv)
        history = []
    # Commit now: the LLM call can take many seconds and must not hold a pooled DB connection.
    await db.commit()

    asked_at = datetime.now(timezone.utc)
    question = HumanMessage(body.message)
    config = {
        "configurable": {"user_id": current_user.user_id},   # read by get_my_orders, never by the LLM
        "recursion_limit": settings.AGENT_MAX_STEPS,
        "run_name": "shop_assistant",
        "metadata": {"conversation_id": str(conv.id)},          # shows up on LangSmith traces
    }
    try:
        result = await agent.ainvoke({"messages": [*history, question]}, config)
        new_messages = result["messages"][len(history) + 1:]
        answer = new_messages[-1].content if new_messages else ""
    except GraphRecursionError:
        logger.warning("agent hit step limit conversation=%s", conv.id)
        new_messages, answer = [], FALLBACK_ANSWER
    except Exception:
        logger.exception("agent failed conversation=%s", conv.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assistant is unavailable right now. Please try again shortly.",
        )

    tools_used = [
        ToolCall(name=c["name"], args=c["args"])
        for m in new_messages if isinstance(m, AIMessage) for c in m.tool_calls
    ]
    answer = (answer if isinstance(answer, str) else str(answer)).strip() or FALLBACK_ANSWER

    # Explicit timestamps: the DB default now() is per-transaction, so both rows would tie
    answered_at = max(datetime.now(timezone.utc), asked_at + timedelta(microseconds=1))
    db.add(Message(conversation_id=conv.id, role="user", content=body.message, created_at=asked_at))
    db.add(Message(
        conversation_id=conv.id, role="assistant", content=answer, created_at=answered_at,
        tool_calls=[t.model_dump() for t in tools_used] or None,
    ))
    await db.commit()
    logger.info("chat answered conversation=%s tools=%s", conv.id, ",".join(t.name for t in tools_used) or "-")
    return ChatResponse(conversation_id=conv.id, answer=answer, tools_used=tools_used)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    rows = await db.scalars(
        select(Conversation).where(Conversation.user_id == uuid.UUID(current_user.user_id))
        .order_by(Conversation.created_at.desc()).limit(50)
    )
    return rows.all()


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def conversation_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await _own_conversation(db, conversation_id, current_user.user_id)
    rows = await db.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return rows.all()
