# supabase_utils.py
import os
import uuid
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # Use the secret service role key
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET_NAME") 

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_to_supabase_and_get_url(file_path, audit_id, step_label, media_type='audio'):
    try:
        file_ext = os.path.splitext(file_path)[1]
        unique_filename = f"{media_type}/{audit_id}/{step_label}_{uuid.uuid4().hex}{file_ext}"
        
        # Upload file
        with open(file_path, "rb") as f:
            res = supabase.storage.from_(SUPABASE_BUCKET).upload(unique_filename, f, {"content-type": "audio/m4a", "upsert": True})

        # Get signed URL
        signed = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(unique_filename, 60 * 60 * 24)
        return signed.get("signedURL")
    except Exception as e:
        print("❌ Supabase upload failed:", e)
        return None