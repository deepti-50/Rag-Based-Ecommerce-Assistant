import uuid
from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base

class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    favorite_categories = Column(JSONB, default=list)
    browsing_history = Column(JSONB, default=list)
    purchase_history_summary = Column(JSONB, default=dict)
