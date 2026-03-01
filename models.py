from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB   # ✅ PostgreSQL JSONB
from sqlalchemy.types import JSON
import uuid

# Cross-database JSON type: JSONB on PostgreSQL, JSON on SQLite/others
JSONB = JSON().with_variant(PG_JSONB, 'postgresql')

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.String, primary_key=True)  # Supabase UUID as string
    email = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    properties = relationship("Property", back_populates="user", cascade="all, delete-orphan")
    audits = relationship("Audit", back_populates="user", cascade="all, delete-orphan")


class Property(db.Model):
    __tablename__ = 'properties'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    street = db.Column(db.String)
    city = db.Column(db.String)
    state = db.Column(db.String)
    zip_code = db.Column(db.String)
    year_built = db.Column(db.Integer)
    sqft = db.Column(db.Integer, nullable=True)
    property_type = db.Column(db.String, nullable=False)

    # Relationships
    user = relationship("User", back_populates="properties")
    audits = relationship("Audit", back_populates="property", cascade="all, delete-orphan")


class Audit(db.Model):
    __tablename__ = 'audits'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
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
    user = relationship("User", back_populates="audits")
    property = relationship("Property", back_populates="audits")
    steps = relationship("AuditStep", back_populates="audit", cascade="all, delete-orphan")
    media = relationship("AuditMedia", back_populates="audit", cascade="all, delete-orphan")
    recommendations = relationship("AuditRecommendation", back_populates="audit", cascade="all, delete-orphan")
    room_measurements = relationship("RoomMeasurement", back_populates="audit", cascade="all, delete-orphan")
class AuditStep(db.Model):
    __tablename__ = "audit_steps"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
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
    # ✅ must match RoomMeasurement.step
    room_measurements = relationship("RoomMeasurement", back_populates="step", cascade="all, delete-orphan", passive_deletes=True)


class AuditMedia(db.Model):
    __tablename__ = "audit_media"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
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


class RoomMeasurement(db.Model):
    __tablename__ = "room_measurements"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id = db.Column(db.Integer, db.ForeignKey('audits.id', ondelete="CASCADE"), nullable=False)
    audit_step_id = db.Column(db.Integer, db.ForeignKey('audit_steps.id', ondelete="CASCADE"), nullable=False)
    area_sqft = db.Column(db.Float, nullable=False)
    polygon_vertices = db.Column(JSONB, nullable=False, default=list)
    source = db.Column(db.String(20), nullable=False)
    confidence_score = db.Column(db.Float, nullable=False)
    quality_metadata = db.Column(JSONB, nullable=False, default=dict)
    user_modified = db.Column(db.Boolean, nullable=False, default=False)
    user_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    audit = relationship("Audit", back_populates="room_measurements")
    step = relationship("AuditStep", back_populates="room_measurements")

    def to_dict(self):
        vertices = []
        for vertex in self.polygon_vertices or []:
            if isinstance(vertex, dict):
                vertices.append({
                    "x_m": vertex.get("x_m") if "x_m" in vertex else vertex.get("x"),
                    "y_m": vertex.get("y_m") if "y_m" in vertex else vertex.get("y"),
                })
            elif isinstance(vertex, (list, tuple)) and len(vertex) >= 2:
                vertices.append({"x_m": vertex[0], "y_m": vertex[1]})
        return {
            "id": self.id,
            "audit_id": self.audit_id,
            "audit_step_id": self.audit_step_id,
            "area_sqft": self.area_sqft,
            "polygon_vertices": vertices,
            "source": self.source,
            "confidence_score": self.confidence_score,
            "quality_metadata": self.quality_metadata or {},
            "user_modified": self.user_modified,
            "user_verified": self.user_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AgentConversation(db.Model):
    __tablename__ = "agent_conversations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    audit_id = db.Column(db.Integer, nullable=False)
    domain = db.Column(db.String, nullable=False)   # insulation, hvac, exterior, interview
    role = db.Column(db.String, nullable=False)     # system, user, assistant
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditRecommendation(db.Model):
    __tablename__ = "audit_recommendations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
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
    
    # Service catalog alignment fields
    # ID of the matched service from the catalog (e.g., 'hvac-ducting-repair')
    service_id = db.Column(db.String(100), nullable=True)
    # Priority order for work sequencing (lower = do first, from catalog)
    order_of_completion = db.Column(db.Integer, nullable=True)
    # Whether this service qualifies for rebates (from catalog)
    rebate_eligible = db.Column(db.Boolean, nullable=True)
    
    # Energy Usage Agent fields
    # Recommendation type: 'upgrade' (default) or 'behavior' (usage change, no cost)
    recommendation_type = db.Column(db.String(20), nullable=False, server_default=sa.text("'upgrade'"))
    # User status: 'interested', 'not_relevant', 'completed', or null (no action taken)
    user_status = db.Column(db.String(20), nullable=True)
    # When user_status was last updated
    user_status_updated_at = db.Column(db.DateTime, nullable=True)

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


# ──────────────────────────────────────────────────────────────────────────────
# Utility Connection & Usage Models (Green Button / Energy Agent)
# ──────────────────────────────────────────────────────────────────────────────

class UtilityConnection(db.Model):
    """
    Tracks a utility data connection for an audit.
    
    Supports multiple providers: SDG&E CMD (direct), UtilityAPI (aggregator),
    and manual entry. OAuth tokens are encrypted at rest.
    """
    __tablename__ = "utility_connections"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    
    # Provider identification
    utility_name = db.Column(db.String(50), nullable=False)  # e.g., 'SDGE', 'PGE', 'SCE'
    provider_name = db.Column(db.String(50), nullable=False)  # 'sdge_cmd', 'utilityapi', 'manual'
    data_scope = db.Column(db.String(20), default='electric')  # 'electric', 'gas', 'both'
    
    # Connection status
    status = db.Column(db.String(30), default='not_connected')
    # Possible values: 'not_connected', 'pending_authorization', 'connected', 
    #                  'sync_in_progress', 'failed', 'revoked'
    
    # OAuth tokens (encrypted)
    access_token_enc = db.Column(db.Text, nullable=True)
    refresh_token_enc = db.Column(db.Text, nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    
    # Provider-specific data (e.g., UtilityAPI authorization_id, SDGE subscription_id)
    provider_metadata = db.Column(JSONB, default=dict)
    
    # OAuth state for security validation
    oauth_state = db.Column(db.String(255), nullable=True)
    
    # Sync timestamps
    last_sync_at = db.Column(db.DateTime, nullable=True)
    last_sync_error = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    audit = relationship("Audit", backref="utility_connections")
    usage_data = relationship("UtilityUsageData", back_populates="connection", cascade="all, delete-orphan")
    interval_data = relationship("UtilityIntervalData", back_populates="connection", cascade="all, delete-orphan")
    usage_summary = relationship("UtilityUsageSummary", back_populates="connection", uselist=False, cascade="all, delete-orphan")

    @property
    def access_token(self):
        """Decrypt and return access token."""
        if not self.access_token_enc:
            return None
        from utils.encryption import decrypt_token
        return decrypt_token(self.access_token_enc)
    
    @access_token.setter
    def access_token(self, value):
        """Encrypt and store access token."""
        if value is None:
            self.access_token_enc = None
        else:
            from utils.encryption import encrypt_token
            self.access_token_enc = encrypt_token(value)
    
    @property
    def refresh_token(self):
        """Decrypt and return refresh token."""
        if not self.refresh_token_enc:
            return None
        from utils.encryption import decrypt_token
        return decrypt_token(self.refresh_token_enc)
    
    @refresh_token.setter
    def refresh_token(self, value):
        """Encrypt and store refresh token."""
        if value is None:
            self.refresh_token_enc = None
        else:
            from utils.encryption import encrypt_token
            self.refresh_token_enc = encrypt_token(value)


class UtilityUsageData(db.Model):
    """
    Raw utility usage data (monthly intervals).
    
    Stores individual billing period records from Green Button data
    or manual entry. Used to compute normalized summaries.
    """
    __tablename__ = "utility_usage_data"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey("utility_connections.id", ondelete="CASCADE"), nullable=False)
    
    # Billing period
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    
    # Usage data
    usage_amount = db.Column(db.Float, nullable=False)  # kWh for electric, therms for gas
    unit = db.Column(db.String(20), default='kWh')  # 'kWh' or 'therms'
    cost_usd = db.Column(db.Float, nullable=True)  # Optional cost
    
    # Data source
    source = db.Column(db.String(30), default='api')  # 'api', 'manual', 'xml_import'
    
    # Raw data preservation (ESPI XML or API response excerpt)
    raw_data = db.Column(JSONB, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    connection = relationship("UtilityConnection", back_populates="usage_data")
    audit = relationship("Audit", backref="utility_usage_records")


class UtilityIntervalData(db.Model):
    """
    High-resolution utility interval data (15-minute or hourly).
    
    Stores granular usage readings from Green Button or UtilityAPI intervals.
    Used for detailed load analysis, TOU optimization, and peak demand tracking.
    
    Note: This can generate many records (2,880 per month for 15-min intervals).
    Consider periodic cleanup or aggregation for older data.
    """
    __tablename__ = "utility_interval_data"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey("utility_connections.id", ondelete="CASCADE"), nullable=False)
    
    # Interval timing (using DateTime for precision)
    interval_start = db.Column(db.DateTime, nullable=False)
    interval_end = db.Column(db.DateTime, nullable=False)
    
    # Usage data
    usage_kwh = db.Column(db.Float, nullable=False)  # kWh for this interval
    
    # Data source identifiers (for deduplication)
    interval_uid = db.Column(db.String(100), nullable=True)  # UtilityAPI interval UID
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    connection = relationship("UtilityConnection", back_populates="interval_data")
    audit = relationship("Audit", backref="utility_interval_records")

    # Index for efficient querying by connection and time range
    __table_args__ = (
        db.Index('ix_interval_connection_time', 'connection_id', 'interval_start'),
    )


class UtilityUsageSummary(db.Model):
    """
    Normalized utility usage summary for agent consumption.
    
    Aggregates raw usage data into annualized metrics with seasonal
    patterns. This is the primary interface for the Energy Usage Agent.
    """
    __tablename__ = "utility_usage_summaries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    audit_id = db.Column(db.Integer, db.ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey("utility_connections.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Summary data
    fuel_type = db.Column(db.String(20), default='electric')  # 'electric', 'gas'
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    
    # Annualized metrics
    annual_usage = db.Column(db.Float, nullable=True)  # kWh or therms
    annual_cost_usd = db.Column(db.Float, nullable=True)
    unit = db.Column(db.String(20), default='kWh')
    
    # Detailed breakdowns (JSONB)
    monthly_breakdown = db.Column(JSONB, default=list)  # [{month, usage, cost_usd, unit}, ...]
    seasonal_pattern = db.Column(JSONB, nullable=True)  # {winter, spring, summer, fall}
    tou_data = db.Column(JSONB, nullable=True)  # Time-of-use data if available
    
    # Data quality indicators
    data_quality_flags = db.Column(JSONB, default=list)  # ['partial_data_6_months', 'data_gaps_detected', ...]
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    connection = relationship("UtilityConnection", back_populates="usage_summary")
    audit = relationship("Audit", backref="utility_usage_summary")

    def to_dict(self):
        """Convert to dictionary for API responses and agent context."""
        return {
            "id": self.id,
            "audit_id": self.audit_id,
            "connection_id": self.connection_id,
            "fuel_type": self.fuel_type,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "annual_usage": self.annual_usage,
            "annual_usage_display": f"{self.annual_usage:,.0f} {self.unit}" if self.annual_usage else None,
            "annual_cost_usd": self.annual_cost_usd,
            "unit": self.unit,
            "monthly_breakdown": self.monthly_breakdown,
            "seasonal_pattern": self.seasonal_pattern,
            "tou_data": self.tou_data,
            "data_quality_flags": self.data_quality_flags,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Solar Simulation Cache Models
# ──────────────────────────────────────────────────────────────────────────────

class PVWattsCache(db.Model):
    """
    Cache for PVWatts API responses.
    
    Stores hourly production profiles (8760 values) by zip code and parameters
    to minimize repeated API calls. Profiles expire after 30 days.
    """
    __tablename__ = "pvwatts_cache"

    id = db.Column(db.Integer, primary_key=True)
    zip_code = db.Column(db.String(10), nullable=False, index=True)
    params_hash = db.Column(db.String(32), nullable=False)  # MD5 of tilt|azimuth|etc
    hourly_profile = db.Column(db.Text, nullable=False)  # JSON array of 8760 floats
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        db.Index('ix_pvwatts_cache_zip_hash', 'zip_code', 'params_hash'),
    )


class ExportRateSchedule(db.Model):
    """
    NEM 3.0 / SBP export rate schedules by utility.
    
    Stores time-varying export compensation rates for solar exports.
    Rates vary by month, hour, and day type (weekday/weekend).
    """
    __tablename__ = "export_rate_schedules"

    id = db.Column(db.Integer, primary_key=True)
    utility = db.Column(db.String(20), nullable=False)  # e.g., 'SDGE', 'PGE', 'SCE'
    effective_date = db.Column(db.Date, nullable=False)
    
    # Rate schedule as JSON: {"rates": {"M-H-W": rate, ...}} where M=month, H=hour, W=0/1
    # e.g., {"rates": {"1-0-0": 0.045, "1-0-1": 0.040, ...}}
    schedule_data = db.Column(JSONB, nullable=False)
    
    # Metadata
    description = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.Index('ix_export_rate_utility_date', 'utility', 'effective_date'),
    )
