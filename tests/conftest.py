# conftest.py
"""
Pytest configuration for backend tests.
Sets up environment variables needed by imports before any test modules load.
"""
import os

# Set required environment variables BEFORE any imports
os.environ.setdefault('SUPABASE_URL', 'https://test.supabase.co')
os.environ.setdefault('SUPABASE_ANON_KEY', 'test-anon-key')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'test-service-role-key')
os.environ.setdefault('SUPABASE_JWT_SECRET', 'test-jwt-secret')
os.environ.setdefault('OPENAI_API_KEY', 'test-openai-key')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('HOMEOWNER_JWT_SECRET', 'test-homeowner-jwt-secret')
