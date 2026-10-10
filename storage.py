import uuid
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

def upload_image(file_bytes, content_type, category):
    ext = content_type.split("/")[-1].replace("jpeg", "jpg")
    path = f"{category}/{uuid.uuid4()}.{ext}"
    response = sb.storage.from_("post-images").upload(
        path,
        file_bytes,
        file_options={"content-type": content_type}
    )
    return response.path

def get_signed_url(path):
    response = sb.storage.from_("post-images").create_signed_url(path, 3600)
    url = response["signedURL"]
    if not url.startswith("https://"):
        url = os.getenv("SUPABASE_URL") + url
    return url

def delete_image(path):
    """Delete an image from Supabase Storage."""
    if not path:
        return True
    try:
        sb.storage.from_("post-images").remove([path])
        return True
    except Exception as e:
        print(f"Failed to delete image {path}: {e}")
        return False
