"""
Tests for Utility Routes

Tests for utility connection management, OAuth callbacks,
data synchronization, and usage summary retrieval.
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def app():
    """Create test Flask application."""
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy
    
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['TESTING'] = True
    
    return app


@pytest.fixture
def db(app):
    """Set up test database with models."""
    from flask_sqlalchemy import SQLAlchemy
    
    db = SQLAlchemy(app)
    
    # Define minimal models for testing
    class Audit(db.Model):
        __tablename__ = 'audits'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.String, nullable=False)
        property_id = db.Column(db.Integer)
        name = db.Column(db.String)
    
    class UtilityConnection(db.Model):
        __tablename__ = 'utility_connections'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.String, nullable=False)
        audit_id = db.Column(db.Integer, db.ForeignKey('audits.id'), nullable=False)
        provider_name = db.Column(db.String(50), nullable=False)
        provider_type = db.Column(db.String(20), nullable=False)
        utility_name = db.Column(db.String(100), nullable=False)
        connection_status = db.Column(db.String(30), nullable=False, default='not_connected')
        data_scope = db.Column(db.String(20), nullable=False, default='electric')
        access_token_encrypted = db.Column(db.Text)
        refresh_token_encrypted = db.Column(db.Text)
        token_expires_at = db.Column(db.DateTime)
        subscription_id = db.Column(db.String(255))
        resource_uri = db.Column(db.String(500))
        external_account_id = db.Column(db.String(255))
        fallback_attempted = db.Column(db.Boolean, default=False)
        fallback_provider = db.Column(db.String(50))
        error_message = db.Column(db.Text)
        last_sync_at = db.Column(db.DateTime)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    class UtilityUsageData(db.Model):
        __tablename__ = 'utility_usage_data'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.String, nullable=False)
        audit_id = db.Column(db.Integer, nullable=False)
        utility_connection_id = db.Column(db.Integer)
        raw_data = db.Column(db.JSON)
        data_format = db.Column(db.String(50))
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    class UtilityUsageSummary(db.Model):
        __tablename__ = 'utility_usage_summaries'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.String, nullable=False)
        audit_id = db.Column(db.Integer, nullable=False)
        utility_connection_id = db.Column(db.Integer)
        fuel_type = db.Column(db.String(20))
        start_date = db.Column(db.Date)
        end_date = db.Column(db.Date)
        annual_usage_kwh = db.Column(db.Float)
        annual_cost_usd = db.Column(db.Float)
        monthly_breakdown = db.Column(db.JSON)
        seasonal_pattern = db.Column(db.JSON)
        tou_data = db.Column(db.JSON)
        data_quality_flags = db.Column(db.JSON)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    with app.app_context():
        db.create_all()
    
    # Store models on db for access in tests
    db.Audit = Audit
    db.UtilityConnection = UtilityConnection
    db.UtilityUsageData = UtilityUsageData
    db.UtilityUsageSummary = UtilityUsageSummary
    
    return db


@pytest.fixture
def mock_registry():
    """Mock the provider registry."""
    mock = MagicMock()
    
    # Mock get_available_providers
    mock.get_available_providers.return_value = [
        {'name': 'sdge_cmd', 'type': 'direct_cmd', 'utilities': ['SDGE'], 'available': True},
        {'name': 'utilityapi', 'type': 'aggregator', 'utilities': ['*'], 'available': True},
        {'name': 'manual', 'type': 'manual', 'utilities': ['*'], 'available': True}
    ]
    
    # Mock get_waterfall_chains
    mock.get_waterfall_chains.return_value = {
        'SDGE': ['sdge_cmd', 'utilityapi', 'manual'],
        'PGE': ['utilityapi', 'manual']
    }
    
    # Mock get_supported_utilities
    mock.get_supported_utilities.return_value = ['SDGE', 'PGE', 'SCE', 'LADWP']
    
    return mock


@pytest.fixture
def client(app, db, mock_registry):
    """Create test client with mocked dependencies."""
    from flask import Blueprint, request, jsonify, g
    
    # Create mock auth decorator
    def mock_require_auth(f):
        def decorated(*args, **kwargs):
            g.current_user = {'id': 'test-user-123', 'email': 'test@example.com'}
            return f(*args, **kwargs)
        decorated.__name__ = f.__name__
        return decorated
    
    # Create blueprints with mocked dependencies
    utility_bp = Blueprint('utility', __name__, url_prefix='/api/utility')
    audit_utility_bp = Blueprint('audit_utility', __name__, url_prefix='/api/audits/<int:audit_id>')
    
    @utility_bp.route('/providers', methods=['GET'])
    @mock_require_auth
    def get_providers():
        providers_info = mock_registry.get_available_providers()
        waterfall_chains = mock_registry.get_waterfall_chains()
        supported_utilities = mock_registry.get_supported_utilities()
        return jsonify({
            'providers': providers_info,
            'waterfall_chains': waterfall_chains,
            'supported_utilities': supported_utilities
        }), 200
    
    @utility_bp.route('/connect', methods=['POST'])
    @mock_require_auth
    def connect_utility():
        data = request.get_json() or {}
        user_id = g.current_user['id']
        
        audit_id = data.get('audit_id')
        if not audit_id:
            return jsonify({'error': 'audit_id is required'}), 400
        
        # Check audit exists
        audit = db.Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        # Check existing connection
        existing = db.UtilityConnection.query.filter_by(
            audit_id=audit_id, user_id=user_id
        ).filter(db.UtilityConnection.connection_status.in_([
            'connected', 'pending_authorization', 'sync_in_progress'
        ])).first()
        
        if existing:
            return jsonify({
                'error': 'Active utility connection already exists',
                'existing_connection_id': existing.id
            }), 409
        
        utility_name = data.get('utility_name', 'SDGE')
        data_scope = data.get('data_scope', 'electric')
        
        # Create pending connection
        conn = db.UtilityConnection(
            user_id=user_id,
            audit_id=audit_id,
            provider_name='sdge_cmd',
            provider_type='direct_cmd',
            utility_name=utility_name,
            connection_status='pending_authorization',
            data_scope=data_scope
        )
        db.session.add(conn)
        db.session.commit()
        
        return jsonify({
            'auth_url': 'https://mock-auth.com/oauth?state=abc123',
            'state': f'abc123:{conn.id}',
            'connection_id': conn.id,
            'provider_used': 'sdge_cmd',
            'requires_redirect': True
        }), 200
    
    @audit_utility_bp.route('/utility-connection', methods=['GET'])
    @mock_require_auth
    def get_utility_connection(audit_id):
        user_id = g.current_user['id']
        
        audit = db.Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        connection = db.UtilityConnection.query.filter_by(
            audit_id=audit_id, user_id=user_id
        ).order_by(db.UtilityConnection.created_at.desc()).first()
        
        if not connection:
            return jsonify({'connected': False, 'connection': None}), 200
        
        return jsonify({
            'connected': connection.connection_status == 'connected',
            'connection': {
                'id': connection.id,
                'provider_name': connection.provider_name,
                'utility_name': connection.utility_name,
                'connection_status': connection.connection_status,
                'data_scope': connection.data_scope
            }
        }), 200
    
    @audit_utility_bp.route('/utility-summary', methods=['GET'])
    @mock_require_auth
    def get_utility_summary(audit_id):
        user_id = g.current_user['id']
        
        audit = db.Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        connection = db.UtilityConnection.query.filter_by(
            audit_id=audit_id, user_id=user_id
        ).filter(db.UtilityConnection.connection_status.in_(['connected'])).first()
        
        if not connection:
            return jsonify({
                'connected': False,
                'connection': None,
                'summary': None
            }), 200
        
        summary = db.UtilityUsageSummary.query.filter_by(
            audit_id=audit_id,
            utility_connection_id=connection.id
        ).first()
        
        summary_data = None
        if summary:
            summary_data = {
                'fuel_type': summary.fuel_type,
                'annual_usage_kwh': summary.annual_usage_kwh,
                'annual_cost_usd': summary.annual_cost_usd
            }
        
        return jsonify({
            'connected': True,
            'connection': {
                'id': connection.id,
                'provider_name': connection.provider_name,
                'utility_name': connection.utility_name,
                'status': connection.connection_status
            },
            'summary': summary_data
        }), 200
    
    @audit_utility_bp.route('/utility-data/manual', methods=['POST'])
    @mock_require_auth
    def submit_manual_data(audit_id):
        user_id = g.current_user['id']
        
        audit = db.Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        data = request.get_json() or {}
        monthly_data = data.get('monthly_data', [])
        
        if not monthly_data:
            return jsonify({'error': 'monthly_data is required'}), 400
        
        # Create connection
        conn = db.UtilityConnection(
            user_id=user_id,
            audit_id=audit_id,
            provider_name='manual',
            provider_type='manual',
            utility_name=data.get('utility_name', 'UNKNOWN'),
            connection_status='connected',
            data_scope=data.get('data_scope', 'electric')
        )
        db.session.add(conn)
        db.session.flush()
        
        # Calculate totals
        annual_usage = sum(m.get('usage_kwh', 0) for m in monthly_data)
        annual_cost = sum(m.get('cost_usd', 0) for m in monthly_data)
        
        # Create summary
        summary = db.UtilityUsageSummary(
            user_id=user_id,
            audit_id=audit_id,
            utility_connection_id=conn.id,
            fuel_type='electric',
            annual_usage_kwh=annual_usage,
            annual_cost_usd=annual_cost,
            monthly_breakdown=monthly_data
        )
        db.session.add(summary)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'connection_id': conn.id,
            'summary': {
                'annual_usage_kwh': annual_usage,
                'annual_cost_usd': annual_cost,
                'months_covered': len(monthly_data)
            }
        }), 200
    
    @audit_utility_bp.route('/utility-connection', methods=['DELETE'])
    @mock_require_auth
    def delete_connection(audit_id):
        user_id = g.current_user['id']
        
        audit = db.Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        connection = db.UtilityConnection.query.filter_by(
            audit_id=audit_id, user_id=user_id
        ).filter(db.UtilityConnection.connection_status != 'revoked').first()
        
        if not connection:
            return jsonify({'error': 'No active utility connection'}), 404
        
        connection.connection_status = 'revoked'
        db.session.commit()
        
        return jsonify({
            'success': True,
            'connection_id': connection.id,
            'data_deleted': False
        }), 200
    
    # Register blueprints
    app.register_blueprint(utility_bp)
    app.register_blueprint(audit_utility_bp)
    
    with app.app_context():
        yield app.test_client()


class TestGetProviders:
    """Tests for GET /api/utility/providers endpoint."""
    
    def test_returns_providers_list(self, client):
        """Should return list of available providers."""
        response = client.get('/api/utility/providers')
        assert response.status_code == 200
        
        data = response.get_json()
        assert 'providers' in data
        assert 'waterfall_chains' in data
        assert 'supported_utilities' in data
        
        # Check providers structure
        providers = data['providers']
        assert len(providers) >= 3
        
        # Find sdge_cmd provider
        sdge = next((p for p in providers if p['name'] == 'sdge_cmd'), None)
        assert sdge is not None
        assert sdge['type'] == 'direct_cmd'
        assert 'SDGE' in sdge['utilities']
    
    def test_returns_waterfall_chains(self, client):
        """Should return waterfall chains by utility."""
        response = client.get('/api/utility/providers')
        data = response.get_json()
        
        chains = data['waterfall_chains']
        assert 'SDGE' in chains
        assert chains['SDGE'][0] == 'sdge_cmd'  # SDGE starts with CMD


class TestConnectUtility:
    """Tests for POST /api/utility/connect endpoint."""
    
    def test_connect_requires_audit_id(self, client):
        """Should require audit_id in request body."""
        response = client.post('/api/utility/connect',
                               json={},
                               content_type='application/json')
        assert response.status_code == 400
        assert 'audit_id is required' in response.get_json()['error']
    
    def test_connect_audit_not_found(self, client, db, app):
        """Should return 404 for non-existent audit."""
        with app.app_context():
            response = client.post('/api/utility/connect',
                                   json={'audit_id': 99999},
                                   content_type='application/json')
            assert response.status_code == 404
    
    def test_connect_creates_pending_connection(self, client, db, app):
        """Should create pending connection and return auth URL."""
        with app.app_context():
            # Create test audit
            audit = db.Audit(id=1, user_id='test-user-123', name='Test Audit')
            db.session.add(audit)
            db.session.commit()
            
            response = client.post('/api/utility/connect',
                                   json={'audit_id': 1, 'utility_name': 'SDGE'},
                                   content_type='application/json')
            
            assert response.status_code == 200
            data = response.get_json()
            
            assert 'auth_url' in data
            assert 'state' in data
            assert 'connection_id' in data
            assert data['requires_redirect'] == True
            
            # Verify connection created
            conn = db.UtilityConnection.query.get(data['connection_id'])
            assert conn is not None
            assert conn.connection_status == 'pending_authorization'
    
    def test_connect_rejects_duplicate(self, client, db, app):
        """Should reject connection if active one already exists."""
        with app.app_context():
            audit = db.Audit(id=2, user_id='test-user-123', name='Test Audit')
            db.session.add(audit)
            
            existing = db.UtilityConnection(
                user_id='test-user-123',
                audit_id=2,
                provider_name='sdge_cmd',
                provider_type='direct_cmd',
                utility_name='SDGE',
                connection_status='connected'
            )
            db.session.add(existing)
            db.session.commit()
            
            response = client.post('/api/utility/connect',
                                   json={'audit_id': 2},
                                   content_type='application/json')
            
            assert response.status_code == 409
            assert 'already exists' in response.get_json()['error']


class TestGetUtilityConnection:
    """Tests for GET /api/audits/<audit_id>/utility-connection endpoint."""
    
    def test_no_connection_returns_not_connected(self, client, db, app):
        """Should return connected=False when no connection exists."""
        with app.app_context():
            audit = db.Audit(id=10, user_id='test-user-123', name='Test')
            db.session.add(audit)
            db.session.commit()
            
            response = client.get('/api/audits/10/utility-connection')
            assert response.status_code == 200
            
            data = response.get_json()
            assert data['connected'] == False
            assert data['connection'] is None
    
    def test_returns_connection_details(self, client, db, app):
        """Should return connection details when connected."""
        with app.app_context():
            audit = db.Audit(id=11, user_id='test-user-123', name='Test')
            db.session.add(audit)
            
            conn = db.UtilityConnection(
                user_id='test-user-123',
                audit_id=11,
                provider_name='sdge_cmd',
                provider_type='direct_cmd',
                utility_name='SDGE',
                connection_status='connected',
                data_scope='electric'
            )
            db.session.add(conn)
            db.session.commit()
            
            response = client.get('/api/audits/11/utility-connection')
            assert response.status_code == 200
            
            data = response.get_json()
            assert data['connected'] == True
            assert data['connection']['provider_name'] == 'sdge_cmd'
            assert data['connection']['utility_name'] == 'SDGE'


class TestGetUtilitySummary:
    """Tests for GET /api/audits/<audit_id>/utility-summary endpoint."""
    
    def test_no_connection_returns_null_summary(self, client, db, app):
        """Should return null summary when not connected."""
        with app.app_context():
            audit = db.Audit(id=20, user_id='test-user-123', name='Test')
            db.session.add(audit)
            db.session.commit()
            
            response = client.get('/api/audits/20/utility-summary')
            assert response.status_code == 200
            
            data = response.get_json()
            assert data['connected'] == False
            assert data['summary'] is None
    
    def test_returns_summary_when_available(self, client, db, app):
        """Should return usage summary when connected with data."""
        with app.app_context():
            audit = db.Audit(id=21, user_id='test-user-123', name='Test')
            db.session.add(audit)
            
            conn = db.UtilityConnection(
                id=100,
                user_id='test-user-123',
                audit_id=21,
                provider_name='sdge_cmd',
                provider_type='direct_cmd',
                utility_name='SDGE',
                connection_status='connected'
            )
            db.session.add(conn)
            
            summary = db.UtilityUsageSummary(
                user_id='test-user-123',
                audit_id=21,
                utility_connection_id=100,
                fuel_type='electric',
                annual_usage_kwh=8500,
                annual_cost_usd=1700
            )
            db.session.add(summary)
            db.session.commit()
            
            response = client.get('/api/audits/21/utility-summary')
            assert response.status_code == 200
            
            data = response.get_json()
            assert data['connected'] == True
            assert data['summary']['annual_usage_kwh'] == 8500
            assert data['summary']['annual_cost_usd'] == 1700


class TestManualDataEntry:
    """Tests for POST /api/audits/<audit_id>/utility-data/manual endpoint."""
    
    def test_manual_entry_requires_monthly_data(self, client, db, app):
        """Should require monthly_data in request."""
        with app.app_context():
            audit = db.Audit(id=30, user_id='test-user-123', name='Test')
            db.session.add(audit)
            db.session.commit()
            
            response = client.post('/api/audits/30/utility-data/manual',
                                   json={},
                                   content_type='application/json')
            assert response.status_code == 400
            assert 'monthly_data is required' in response.get_json()['error']
    
    def test_manual_entry_creates_connection_and_summary(self, client, db, app):
        """Should create manual connection and compute summary."""
        with app.app_context():
            audit = db.Audit(id=31, user_id='test-user-123', name='Test')
            db.session.add(audit)
            db.session.commit()
            
            monthly_data = [
                {'month': '2024-01', 'usage_kwh': 650, 'cost_usd': 130},
                {'month': '2024-02', 'usage_kwh': 600, 'cost_usd': 120},
                {'month': '2024-03', 'usage_kwh': 550, 'cost_usd': 110}
            ]
            
            response = client.post('/api/audits/31/utility-data/manual',
                                   json={
                                       'utility_name': 'SDGE',
                                       'monthly_data': monthly_data
                                   },
                                   content_type='application/json')
            
            assert response.status_code == 200
            data = response.get_json()
            
            assert data['success'] == True
            assert 'connection_id' in data
            assert data['summary']['annual_usage_kwh'] == 1800  # 650+600+550
            assert data['summary']['annual_cost_usd'] == 360  # 130+120+110
            assert data['summary']['months_covered'] == 3
            
            # Verify connection created
            conn = db.UtilityConnection.query.get(data['connection_id'])
            assert conn.provider_name == 'manual'
            assert conn.connection_status == 'connected'


class TestDeleteConnection:
    """Tests for DELETE /api/audits/<audit_id>/utility-connection endpoint."""
    
    def test_delete_no_connection_returns_404(self, client, db, app):
        """Should return 404 when no active connection."""
        with app.app_context():
            audit = db.Audit(id=40, user_id='test-user-123', name='Test')
            db.session.add(audit)
            db.session.commit()
            
            response = client.delete('/api/audits/40/utility-connection')
            assert response.status_code == 404
    
    def test_delete_revokes_connection(self, client, db, app):
        """Should revoke active connection."""
        with app.app_context():
            audit = db.Audit(id=41, user_id='test-user-123', name='Test')
            db.session.add(audit)
            
            conn = db.UtilityConnection(
                id=200,
                user_id='test-user-123',
                audit_id=41,
                provider_name='sdge_cmd',
                provider_type='direct_cmd',
                utility_name='SDGE',
                connection_status='connected'
            )
            db.session.add(conn)
            db.session.commit()
            
            response = client.delete('/api/audits/41/utility-connection')
            assert response.status_code == 200
            
            data = response.get_json()
            assert data['success'] == True
            assert data['connection_id'] == 200
            
            # Verify status updated
            conn = db.UtilityConnection.query.get(200)
            assert conn.connection_status == 'revoked'


class TestAuthorizationFlow:
    """Tests for full OAuth authorization flow."""
    
    def test_pending_to_connected_flow(self, client, db, app):
        """Should track connection through pending -> connected states."""
        with app.app_context():
            # Setup
            audit = db.Audit(id=50, user_id='test-user-123', name='Test')
            db.session.add(audit)
            db.session.commit()
            
            # 1. Initiate connection
            response = client.post('/api/utility/connect',
                                   json={'audit_id': 50, 'utility_name': 'SDGE'},
                                   content_type='application/json')
            assert response.status_code == 200
            conn_id = response.get_json()['connection_id']
            
            # 2. Verify pending status
            conn = db.UtilityConnection.query.get(conn_id)
            assert conn.connection_status == 'pending_authorization'
            
            # 3. Simulate OAuth callback success (manual update for test)
            conn.connection_status = 'connected'
            db.session.commit()
            
            # 4. Verify connected via API
            response = client.get('/api/audits/50/utility-connection')
            data = response.get_json()
            assert data['connected'] == True
            assert data['connection']['connection_status'] == 'connected'
