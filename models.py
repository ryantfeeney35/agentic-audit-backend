# models.py
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
    utility_bill_url = db.Column(db.String, nullable=True)
    utility_bill_name = db.Column(db.String, nullable=True)

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


class AuditStep(db.Model):
    __tablename__ = 'audit_steps'
    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey('audits.id', ondelete='CASCADE'), nullable=False)
    step_type = db.Column(db.String, nullable=False)  # e.g., 'exterior', 'attic'
    label = db.Column(db.String, nullable=True)       # e.g., 'North Side', 'Attic Access Hatch'
    is_completed = db.Column(db.Boolean, default=False)
    not_accessible = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text, nullable=True)

    # Relationships
    audit = relationship('Audit', back_populates='steps')
    media = relationship('AuditMedia', back_populates='step', cascade="all, delete-orphan")
    findings = relationship('AuditFinding', back_populates='step', cascade="all, delete-orphan")


class AuditMedia(db.Model):
    __tablename__ = 'audit_media'
    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey('audits.id'), nullable=False)
    step_id = db.Column(db.Integer, db.ForeignKey('audit_steps.id'), nullable=True)
    step_type = db.Column(db.String, nullable=False)
    side = db.Column(db.String, nullable=True)
    media_url = db.Column(db.String, nullable=True)
    file_name = db.Column(db.String, nullable=True)  # ✅ ADD THIS LINE
    media_type = db.Column(db.String, nullable=True)  # e.g., 'photo', 'video'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    step = relationship('AuditStep', back_populates='media')


class AuditFinding(db.Model):
    __tablename__ = 'audit_findings'
    id = db.Column(db.Integer, primary_key=True)
    step_id = db.Column(db.Integer, db.ForeignKey('audit_steps.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String, nullable=True)
    description = db.Column(db.Text, nullable=True)
    recommendation = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String, nullable=True)  # e.g., 'low', 'medium', 'high'
    source = db.Column(db.String, nullable=True)    # e.g., 'AI', 'Inspector'

    # Relationships
    step = relationship('AuditStep', back_populates='findings')