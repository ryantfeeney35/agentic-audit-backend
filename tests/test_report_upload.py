import io
import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client


def test_upload_report_pdf_happy_path(monkeypatch, client):
    # Monkeypatch Supabase upload helper to no-op
    import routes.report_routes as rr

    def fake_upload(path_in_bucket, data, content_type):
        return None

    monkeypatch.setattr(rr, "_upload_bytes_to_supabase", fake_upload)

    data = {
        'file': (io.BytesIO(b'%PDF-1.7 test content'), 'test.pdf', 'application/pdf')
    }
    resp = client.post('/api/audits/123/report/upload', content_type='multipart/form-data', data=data)
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'url' in body and body['url'].endswith('.pdf')


def test_upload_report_rejects_non_pdf(client):
    data = {
        'file': (io.BytesIO(b'not a pdf'), 'notpdf.txt', 'text/plain')
    }
    resp = client.post('/api/audits/123/report/upload', content_type='multipart/form-data', data=data)
    assert resp.status_code == 400
    body = resp.get_json()
    assert 'error' in body
