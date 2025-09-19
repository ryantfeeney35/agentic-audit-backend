# supabase_utils.py
import os
import uuid
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # Use the secret service role key
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET_NAME") 

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_to_supabase_and_get_url(file_path, audit_id, step_label, media_type='audio', step_type=None, side=None):
    try:
        file_ext = os.path.splitext(file_path)[1]
        filename_base = f"{media_type}/{audit_id}/{step_label.replace(' ', '_')}_{uuid.uuid4().hex}"
        full_filename = f"{filename_base}{file_ext}"

        # Read file
        with open(file_path, "rb") as f:
            file_content = f.read()

        # Upload to Supabase
        res = supabase.storage.from_(SUPABASE_BUCKET).upload(
            path=full_filename,
            file=file_content,
            file_options={"content-type": "audio/m4a"}  # ✅ No "upsert" here
        )

        if hasattr(res, "error") and res.error:
            raise Exception(res.error.message if hasattr(res.error, "message") else str(res.error))

        # Create signed URL
        signed = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(full_filename, 60 * 60 * 24)

        if not signed or not signed.get("signedURL"):
            raise Exception("Signed URL not returned")

        return signed.get("signedURL")

    except Exception as e:
        print("❌ Supabase upload failed:", str(e))
        return None