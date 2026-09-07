import uuid
from sqlalchemy import Column, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base

class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    favorite_categories = Column(JSON, default=[])
    browsing_history = Column(JSON, default=[])
    purchase_history_summary = Column(JSON, default={})
