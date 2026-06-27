from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.core.orm import Base


class PortalSavedReport(Base):
    __tablename__ = "portal_saved_reports"

    id = Column(String(64), primary_key=True)
    title = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    sql_content = Column(Text, nullable=False)
    dataset_id = Column(BigInteger, nullable=True)
    data_source = Column(String(100), nullable=False)
    original_query = Column(Text, nullable=True)
    mode = Column(String(32), nullable=False, default="static_sql")
    sql_template = Column(Text, nullable=True)
    params_schema = Column(JSON, nullable=True)
    default_params = Column(JSON, nullable=True)
    analysis_mode = Column(String(32), nullable=False, default="manual")
    tags = Column(JSON, nullable=True)
    owner_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    owner_name = Column(String(100), nullable=True)
    visibility = Column(String(32), nullable=False, default="private")
    status = Column(String(32), nullable=False, default="active")
    last_run_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    shares = relationship(
        "PortalSavedReportShare",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_portal_saved_reports_owner_updated", "owner_user_id", "updated_at"),
        Index("idx_portal_saved_reports_visibility", "visibility"),
    )


class PortalSavedReportShare(Base):
    __tablename__ = "portal_saved_report_shares"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(64), ForeignKey("portal_saved_reports.id"), nullable=False, index=True)
    target_type = Column(String(16), nullable=False)
    target_id = Column(BigInteger, nullable=False, index=True)
    permission = Column(String(16), nullable=False, default="run")
    created_by = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    report = relationship("PortalSavedReport", back_populates="shares")

    __table_args__ = (
        Index("idx_portal_saved_report_shares_target", "target_type", "target_id"),
        Index("ux_portal_saved_report_share_target", "report_id", "target_type", "target_id", unique=True),
    )


class PortalSavedReportUserPref(Base):
    __tablename__ = "portal_saved_report_user_prefs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(64), ForeignKey("portal_saved_reports.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    is_favorite = Column(Boolean, nullable=False, default=False)
    pinned_at = Column(DateTime, nullable=True)
    last_viewed_at = Column(DateTime, nullable=True)
    run_count = Column(Integer, nullable=False, default=0)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        Index("ux_portal_saved_report_user_pref", "report_id", "user_id", unique=True),
        Index("idx_portal_saved_report_user_pref_user", "user_id", "pinned_at", "last_run_at"),
    )
