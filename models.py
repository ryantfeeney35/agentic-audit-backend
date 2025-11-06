from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB   # ✅ PostgreSQL JSONB

db = SQLAlchemy()


class Property(db.Model):
    __tablename__ = 'properties'

    id = db.Column(db.Integer, primary_key=True)
    street = db.Column(db.String)
    city = db.Column(db.String)
    state = db.Column(db.String)
    zip_code = db.Column(db.String)
    year_built = db.Column(db.Integer)
    sqft = db.Column(db.Integer, nullable=True)
    property_type = db.Column(db.String, nullable=False)

    # Relationships
    audits = relationship("Audit", back_populates="property", cascade="all, delete-orphan")


class Audit(db.Model):
    __tablename__ = 'audits'

    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete="CASCADE"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    auditor_name = db.Column(db.String, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    audit_type = db.Column(db.String(50), nullable=False, default="energy_audit")  
    # values: "energy_audit", "home_inspection_energy_audit"
    # ROI defaults/settings at the audit level (e.g., energy_rate, climate, horizon)
    roi_defaults = db.Column(JSONB, default=dict)

    # Relationships
    property = relationship("Property", back_populates="audits")
    steps = relationship("AuditStep", back_populates="audit", cascade="all, delete-orphan")
    media = relationship("AuditMedia", back_populates="audit", cascade="all, delete-orphan")
    recommendations = relationship("AuditRecommendation", back_populates="audit", cascade="all, delete-orphan")
class AuditStep(db.Model):
    __tablename__ = "audit_steps"

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    step_type = db.Column(db.String, nullable=False)
    label = db.Column(db.String, nullable=False)
    status = db.Column(db.String, default="Not Started")

    meta = db.Column(JSONB, default=dict)
    summary = db.Column(db.Text, nullable=True)
    ai_summary = db.Column(JSONB, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ✅ must match Audit.steps
    audit = relationship("Audit", back_populates="steps")
    # ✅ must match AuditMedia.step
    media = relationship("AuditMedia", back_populates="step", cascade="all, delete-orphan")


class AuditMedia(db.Model):
    __tablename__ = "audit_media"

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    step_id = db.Column(db.Integer, db.ForeignKey("audit_steps.id", ondelete="CASCADE"), nullable=False)

    media_url = db.Column(db.String, nullable=False)
    file_name = db.Column(db.String, nullable=False)
    media_type = db.Column(db.String, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    # AI-generated caption for the media (one-line summary)
    ai_caption = db.Column(db.Text, nullable=True)
    # AI embedding vector or metadata (JSONB) — stored as list of floats or dict
    ai_embedding = db.Column(JSONB, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ✅ must match Audit.media
    audit = relationship("Audit", back_populates="media")
    # ✅ must match AuditStep.media
    step = relationship("AuditStep", back_populates="media")


class AgentConversation(db.Model):
    __tablename__ = "agent_conversations"

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, nullable=False)
    domain = db.Column(db.String, nullable=False)   # insulation, hvac, exterior, interview
    role = db.Column(db.String, nullable=False)     # system, user, assistant
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditRecommendation(db.Model):
    __tablename__ = "audit_recommendations"

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    step_type = db.Column(db.String(50), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    # Optional human-refined summary recorded/uploaded by auditors (transcribed + refined)
    summary_override = db.Column(db.Text, nullable=True)
    annual_savings_usd = db.Column(db.Float)
    upgrade_cost_usd = db.Column(db.Float)
    payback_years = db.Column(db.Float)
    # Optional integer to allow auditors to persist a custom display order.
    display_order = db.Column(db.Integer, nullable=True)
    # Allow auditors to hide recommendations without deleting them.
    is_hidden = db.Column(db.Boolean, nullable=False, server_default=sa.text('false'))
    # Source of recommendation: 'audio' when derived from recorded audio/transcripts, 'ai' otherwise.
    source = db.Column(db.String, nullable=False, server_default=sa.text("'ai'"))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    # Optional associated media (a suggested photo/video) for this recommendation
    recommended_media_id = db.Column(db.Integer, db.ForeignKey("audit_media.id", ondelete="SET NULL"), nullable=True)
    # Track whether the recommended_media was auto-suggested or auditor-selected
    recommended_media_source = db.Column(db.String, nullable=True)
    # Per-recommendation ROI inputs (e.g., attic_current_r, attic_target_r, net_upgrade_cost_usd, attic_area_sqft)
    roi_inputs = db.Column(JSONB, default=dict)

    audit = relationship("Audit", back_populates="recommendations")
    # relationship to the suggested media (may be None)
    recommended_media = relationship("AuditMedia", foreign_keys=[recommended_media_id], uselist=False)


class Contractor(db.Model):
    __tablename__ = "contractors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120), nullable=False)  # phone, email, etc.
    step_type = db.Column(db.String(50), nullable=False)  # e.g., insulation, hvac, exterior
    created_at = db.Column(db.DateTime, default=datetime.utcnow)