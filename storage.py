import os
import uuid
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
    return response.signed_url