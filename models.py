from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from datetime import datetime

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

    # Relationships
    audits = relationship('Audit', back_populates='property', cascade="all, delete-orphan")


class Audit(db.Model):
    __tablename__ = 'audits'
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    auditor_name = db.Column(db.String, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    property = relationship('Property', back_populates='audits')
    steps = relationship('AuditStep', back_populates='audit', cascade="all, delete-orphan")
    media = relationship('AuditMedia', back_populates='audit', cascade="all, delete-orphan")
    recommendations = relationship('AuditRecommendation', back_populates='audit', cascade="all, delete-orphan")


class AuditStep(db.Model):
    __tablename__ = 'audit_steps'
    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey('audits.id', ondelete='CASCADE'), nullable=False)
    step_type = db.Column(db.String, nullable=False)  # e.g., 'exterior', 'attic'
    label = db.Column(db.String, nullable=True)       # e.g., 'North Side', 'Attic Access Hatch'
    status = db.Column(db.String(20), default="Not Started")  # New unified status
    notes = db.Column(db.Text, nullable=True)

    # Relationships
    audit = relationship('Audit', back_populates='steps')
    media = relationship('AuditMedia', back_populates='step', cascade="all, delete-orphan")


class AuditMedia(db.Model):
    __tablename__ = 'audit_media'
    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey('audits.id', ondelete="CASCADE"), nullable=False)
    step_id = db.Column(db.Integer, db.ForeignKey('audit_steps.id', ondelete="CASCADE"), nullable=True)
    step_type = db.Column(db.String, nullable=False)
    side = db.Column(db.String, nullable=True)
    media_url = db.Column(db.String, nullable=True)
    file_name = db.Column(db.String, nullable=True)
    media_type = db.Column(db.String, nullable=True)  # e.g., 'photo', 'video'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    summary = db.Column(db.Text)

    # Relationships
    audit = relationship('Audit', back_populates='media')
    step = relationship('AuditStep', back_populates='media')


class AgentConversation(db.Model):
    __tablename__ = "agent_conversations"

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, nullable=False)
    domain = db.Column(db.String, nullable=False)
    role = db.Column(db.String, nullable=False)   # system, user, assistant
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditRecommendation(db.Model):
    __tablename__ = "audit_recommendations"

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    step_type = db.Column(db.String(50), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    annual_savings_usd = db.Column(db.Float)
    upgrade_cost_usd = db.Column(db.Float)
    payback_years = db.Column(db.Float)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    audit = relationship("Audit", back_populates="recommendations")

class Contractor(db.Model):
    __tablename__ = "contractors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120), nullable=False)  # phone, email, etc.
    step_type = db.Column(db.String(50), nullable=False)  # e.g., insulation, hvac, exterior
    created_at = db.Column(db.DateTime, default=datetime.utcnow)