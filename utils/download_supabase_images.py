import os
import requests
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# -------------------------------
# CONFIGURATION
# -------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fsjajxinchyumthqylcv.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "YOUR_KEY"
BUCKET_NAME = "uat"                    # e.g. "audit-media"
PREFIX = "22_"                         # Only files STARTING with this prefix
LOCAL_DIR = "downloaded_images"        # Local folder for saving files
PAGE_SIZE = 100                        # Supabase max per page
# -------------------------------

def list_all_files(supabase: Client, bucket: str) -> list[dict]:
    """Paginate through the entire bucket (top level) using offset."""
    all_files = []
    offset = 0

    while True:
        batch = supabase.storage.from_(bucket).list(
            path="",  # top-level; change if you need a subfolder
            options={
                "limit": PAGE_SIZE,
                "offset": offset,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        # Some SDK versions may return None instead of [] when empty
        batch = batch or []

        if not batch:
            break

        all_files.extend(batch)
        print(f"Fetched {len(batch)} files (total so far: {len(all_files)})")

        if len(batch) < PAGE_SIZE:
            break  # last page
        offset += PAGE_SIZE

    return all_files

def main():
    os.makedirs(LOCAL_DIR, exist_ok=True)

    print(f"Connecting to Supabase bucket '{BUCKET_NAME}'...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching file list with pagination…")
    files = list_all_files(supabase, BUCKET_NAME)

    if not files:
        print("No files found.")
        return

    print(f"{len(files)} files found in total.")
    # Only names starting with PREFIX
    target_files = [f for f in files if (f.get("name") or "").startswith(PREFIX)]
    print(f"Found {len(target_files)} files starting with '{PREFIX}'")

    for i, file in enumerate(target_files, 1):
        file_name = file["name"]
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{file_name}"

        try:
            res = requests.get(public_url, timeout=60)
            res.raise_for_status()

            local_path = os.path.join(LOCAL_DIR, os.path.basename(file_name))
            with open(local_path, "wb") as fh:
                fh.write(res.content)

            print(f"[{i}/{len(target_files)}] ✅ Downloaded {file_name}")
        except Exception as e:
            print(f"[{i}/{len(target_files)}] ❌ Failed {file_name}: {e}")

    print(f"\n✅ Finished downloading {len(target_files)} images to '{LOCAL_DIR}'")

if __name__ == "__main__":
    main()