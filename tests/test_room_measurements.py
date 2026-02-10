import uuid
from datetime import datetime

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

import auth
import routes.measurement_routes as measurement_routes


@pytest.fixture
def measurement_app(monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    test_db = SQLAlchemy(app)

    class TestAudit(test_db.Model):
        __tablename__ = 'audits'
        id = test_db.Column(test_db.Integer, primary_key=True)
        user_id = test_db.Column(test_db.String, nullable=False)

    class TestRoomMeasurement(test_db.Model):
        __tablename__ = 'room_measurements'
        id = test_db.Column(test_db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
        audit_id = test_db.Column(test_db.Integer, test_db.ForeignKey('audits.id'), nullable=False)
        room_id = test_db.Column(test_db.String, nullable=False)
        area_sqft = test_db.Column(test_db.Float, nullable=False)
        polygon_vertices = test_db.Column(test_db.JSON, default=list)
        source = test_db.Column(test_db.String, nullable=False)
        confidence_score = test_db.Column(test_db.Float, nullable=False)
        quality_metadata = test_db.Column(test_db.JSON, default=dict)
        user_modified = test_db.Column(test_db.Boolean, default=False)
        user_verified = test_db.Column(test_db.Boolean, default=False)
        created_at = test_db.Column(test_db.DateTime, default=datetime.utcnow)
        updated_at = test_db.Column(test_db.DateTime, default=datetime.utcnow)

        def to_dict(self):
            vertices = []
            for vertex in self.polygon_vertices or []:
                if isinstance(vertex, dict):
                    vertices.append({
                        'x_m': vertex.get('x_m') if 'x_m' in vertex else vertex.get('x'),
                        'y_m': vertex.get('y_m') if 'y_m' in vertex else vertex.get('y'),
                    })
                elif isinstance(vertex, (list, tuple)) and len(vertex) >= 2:
                    vertices.append({'x_m': vertex[0], 'y_m': vertex[1]})
            return {
                'id': self.id,
                'audit_id': self.audit_id,
                'room_id': self.room_id,
                'area_sqft': self.area_sqft,
                'polygon_vertices': vertices,
                'source': self.source,
                'confidence_score': self.confidence_score,
                'quality_metadata': self.quality_metadata or {},
                'user_modified': self.user_modified,
                'user_verified': self.user_verified,
                'created_at': self.created_at.isoformat() if self.created_at else None,
                'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            }

    with app.app_context():
        test_db.create_all()
        audit = TestAudit(user_id='test-user')
        test_db.session.add(audit)
        test_db.session.commit()

    # Patch measurement routes to use the test database/models
    monkeypatch.setattr(measurement_routes, 'db', test_db)
    monkeypatch.setattr(measurement_routes, 'Audit', TestAudit)
    monkeypatch.setattr(measurement_routes, 'RoomMeasurement', TestRoomMeasurement)

    def fake_validate(token):
        if token == 'valid-token':
            return {'id': 'test-user', 'email': 'auditor@example.com'}
        return None

    monkeypatch.setattr(auth, 'validate_token', fake_validate)

    app.register_blueprint(measurement_routes.bp, url_prefix='/api')

    return app, test_db, TestRoomMeasurement


@pytest.fixture
def client(measurement_app):
    app, _, _ = measurement_app
    return app.test_client()


@pytest.fixture
def auth_header():
    return {'Authorization': 'Bearer valid-token'}


def test_create_measurement_and_fetch_latest(client, measurement_app, auth_header):
    app, db, Measurement = measurement_app

    payload = {
        'source': 'arkit',
        'areaSqFt': 145.2,
        'verticesMeters': [
            {'x': 0, 'y': 0},
            {'x': 1.2, 'y': 0},
            {'x': 1.2, 'y': 2.5},
        ],
        'confidenceScore': 0.82,
        'quality': {
            'trackingLostCount': 1,
            'avgTrackingQuality': 0.7,
            'deviceModel': 'iPhone 15 Pro'
        },
        'userModified': True,
        'userVerified': False
    }

    response = client.post('/api/audits/1/rooms/living-room/measurements', json=payload, headers=auth_header)
    assert response.status_code == 201
    measurement_id = response.json['measurement']['id']
    assert response.json['measurement']['polygon_vertices'][0]['x_m'] == 0
    assert response.json['measurement']['quality_metadata']['device_model'] == 'iPhone 15 Pro'

    latest_resp = client.get('/api/audits/1/rooms/living-room/measurements/latest', headers=auth_header)
    assert latest_resp.status_code == 200
    assert latest_resp.json['measurement']['id'] == measurement_id
    assert latest_resp.json['measurement']['room_id'] == 'living-room'

    with app.app_context():
        stored = Measurement.query.get(measurement_id)
        assert stored is not None
        assert stored.room_id == 'living-room'
        assert stored.user_modified is True
        assert stored.user_verified is False
        assert stored.quality_metadata['tracking_lost_count'] == 1


def test_measurement_summary_aggregates_latest(client, measurement_app, auth_header):
    app, _, _ = measurement_app

    base_payload = {
        'source': 'arkit',
        'verticesMeters': [
            {'x': 0, 'y': 0},
            {'x': 2, 'y': 0},
            {'x': 2, 'y': 3}
        ],
        'confidenceScore': 0.9,
        'areaSqFt': 180.0,
        'userVerified': True,
    }
    # Room 1 initial and updated measurement
    client.post('/api/audits/1/rooms/kitchen/measurements', json=base_payload, headers=auth_header)
    updated_payload = dict(base_payload)
    updated_payload['areaSqFt'] = 200.0
    updated_payload['confidenceScore'] = 0.95
    client.post('/api/audits/1/rooms/kitchen/measurements', json=updated_payload, headers=auth_header)

    # Room 2 manual measurement
    manual_payload = {
        'source': 'manual',
        'areaSqM': 10,
        'confidenceScore': 0.5,
        'userVerified': False,
        'userModified': True
    }
    client.post('/api/audits/1/rooms/bedroom/measurements', json=manual_payload, headers=auth_header)

    summary_resp = client.get('/api/audits/1/measurements/summary', headers=auth_header)
    assert summary_resp.status_code == 200
    summary = summary_resp.json
    assert summary['totals']['rooms'] == 2
    # Latest kitchen measurement should be 200 sqft + manual (~107.639) -> approx 307.64
    assert summary['totals']['measured_area_sqft'] == pytest.approx(307.64, rel=1e-3)
    assert summary['totals']['verified_rooms'] == 1
    assert summary['totals']['manual_entries'] == 1
    assert len(summary['rooms']) == 2
    kitchen_entry = next(item for item in summary['rooms'] if item['room_id'] == 'kitchen')
    assert kitchen_entry['latest_measurement']['area_sqft'] == 200.0


def test_create_measurement_validation_errors(client, auth_header):
    response = client.post('/api/audits/1/rooms/garage/measurements', json={
        'source': 'unsupported',
        'confidenceScore': 1.2
    }, headers=auth_header)
    assert response.status_code == 400
    assert 'source must be one of' in response.json['details'][0]
    assert any('confidence_score' in msg for msg in response.json['details'])


def test_auth_required(client):
    response = client.post('/api/audits/1/rooms/lab/measurements', json={}, headers={})
    assert response.status_code == 401
