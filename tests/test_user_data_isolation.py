# test_user_data_isolation.py
import pytest
from unittest.mock import patch, MagicMock
from flask import Flask
from models import User, Property, Audit, db
from routes.property_routes import bp as property_bp
from routes.audit_routes import bp as audit_bp
import jwt
from datetime import datetime, timedelta


class TestUserDataIsolation:
    @pytest.fixture
    def app(self):
        """Create test Flask app with routes"""
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        db.init_app(app)
        app.register_blueprint(property_bp, url_prefix='/api')
        app.register_blueprint(audit_bp, url_prefix='/api')
        
        with app.app_context():
            db.create_all()
            
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def create_token(self, user_id, email):
        """Create JWT token for user"""
        payload = {
            'sub': user_id,
            'email': email,
            'aud': 'authenticated',
            'exp': datetime.utcnow() + timedelta(hours=1)
        }
        return jwt.encode(payload, 'test-secret', algorithm='HS256')

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    @patch('auth.db')
    def test_users_only_see_own_properties(self, mock_db, client, app):
        """Test that users can only see their own properties"""
        with app.app_context():
            # Create test users
            user1 = User(id='user-1', email='user1@example.com')
            user2 = User(id='user-2', email='user2@example.com')
            db.session.add_all([user1, user2])
            
            # Create properties for each user
            prop1_user1 = Property(id=1, user_id='user-1', street='123 User1 St', city='City1', state='CA', zip_code='12345', property_type='single_family')
            prop2_user1 = Property(id=2, user_id='user-1', street='456 User1 Ave', city='City1', state='CA', zip_code='12346', property_type='condo')
            prop1_user2 = Property(id=3, user_id='user-2', street='789 User2 Blvd', city='City2', state='NY', zip_code='67890', property_type='townhouse')
            prop2_user2 = Property(id=4, user_id='user-2', street='321 User2 Ln', city='City2', state='NY', zip_code='67891', property_type='single_family')
            
            db.session.add_all([prop1_user1, prop2_user1, prop1_user2, prop2_user2])
            db.session.commit()
            
            # Mock auth for user 1
            mock_db.session.get.return_value = user1
            
            token1 = self.create_token('user-1', 'user1@example.com')
            response1 = client.get('/api/properties', headers={
                'Authorization': f'Bearer {token1}'
            })
            
            assert response1.status_code == 200
            properties1 = response1.json
            assert len(properties1) == 2
            assert all(p['id'] in [1, 2] for p in properties1)
            
            # Mock auth for user 2
            mock_db.session.get.return_value = user2
            
            token2 = self.create_token('user-2', 'user2@example.com')
            response2 = client.get('/api/properties', headers={
                'Authorization': f'Bearer {token2}'
            })
            
            assert response2.status_code == 200
            properties2 = response2.json
            assert len(properties2) == 2
            assert all(p['id'] in [3, 4] for p in properties2)

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    @patch('auth.db')
    def test_user_cannot_access_other_users_property(self, mock_db, client, app):
        """Test that user cannot access another user's property"""
        with app.app_context():
            # Create test users and properties
            user1 = User(id='user-1', email='user1@example.com')
            user2 = User(id='user-2', email='user2@example.com')
            db.session.add_all([user1, user2])
            
            prop_user1 = Property(id=1, user_id='user-1', street='123 User1 St', city='City1', state='CA', zip_code='12345', property_type='single_family')
            db.session.add(prop_user1)
            db.session.commit()
            
            # Mock auth for user 2 (different user)
            mock_db.session.get.return_value = user2
            
            token2 = self.create_token('user-2', 'user2@example.com')
            
            # Try to access user1's property
            response = client.get('/api/properties/1', headers={
                'Authorization': f'Bearer {token2}'
            })
            
            assert response.status_code == 403
            assert 'access denied' in response.json['error'].lower()

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    @patch('auth.db')
    def test_user_cannot_modify_other_users_property(self, mock_db, client, app):
        """Test that user cannot modify another user's property"""
        with app.app_context():
            # Create test users and properties
            user1 = User(id='user-1', email='user1@example.com')
            user2 = User(id='user-2', email='user2@example.com')
            db.session.add_all([user1, user2])
            
            prop_user1 = Property(id=1, user_id='user-1', street='123 User1 St', city='City1', state='CA', zip_code='12345', property_type='single_family')
            db.session.add(prop_user1)
            db.session.commit()
            
            # Mock auth for user 2 (different user)
            mock_db.session.get.return_value = user2
            
            token2 = self.create_token('user-2', 'user2@example.com')
            
            # Try to update user1's property
            update_data = {'street': 'Hacked Street'}
            response = client.put('/api/properties/1', 
                                json=update_data,
                                headers={'Authorization': f'Bearer {token2}'})
            
            assert response.status_code == 403
            assert 'access denied' in response.json['error'].lower()
            
            # Try to delete user1's property
            response = client.delete('/api/properties/1', 
                                   headers={'Authorization': f'Bearer {token2}'})
            
            assert response.status_code == 403
            assert 'access denied' in response.json['error'].lower()

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    @patch('auth.db')
    def test_automatic_user_id_assignment(self, mock_db, client, app):
        """Test that user_id is automatically assigned to new records"""
        with app.app_context():
            user1 = User(id='user-1', email='user1@example.com')
            db.session.add(user1)
            db.session.commit()
            
            # Mock auth for user 1
            mock_db.session.get.return_value = user1
            
            token1 = self.create_token('user-1', 'user1@example.com')
            
            # Create new property
            property_data = {
                'street': '123 Test St',
                'city': 'Test City',
                'state': 'CA',
                'zip_code': '12345',
                'property_type': 'single_family',
                'year_built': 2000,
                'sqft': 2000
            }
            
            response = client.post('/api/properties',
                                 json=property_data,
                                 headers={'Authorization': f'Bearer {token1}'})
            
            assert response.status_code == 201
            created_property = response.json
            
            # Verify property was created with correct user_id
            property_in_db = Property.query.get(created_property['id'])
            assert property_in_db.user_id == 'user-1'

if __name__ == '__main__':
    pytest.main([__file__])