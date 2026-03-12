from pathlib import Path

from src.services.storage_repo import upload_file, download_file, delete_file

BUCKET = "student-inputs"
REMOTE_PATH = "test/hello.txt"
LOCAL_SOURCE = Path("hello.txt")
LOCAL_DOWNLOADED = Path("tmp/downloaded_hello.txt")

LOCAL_SOURCE.write_text("hello from testmap", encoding="utf-8")

print("Uploading...")
upload_file(BUCKET, REMOTE_PATH, LOCAL_SOURCE, content_type="text/plain")

print("Downloading...")
download_file(BUCKET, REMOTE_PATH, LOCAL_DOWNLOADED)

content = LOCAL_DOWNLOADED.read_text(encoding="utf-8")
print("Downloaded content:", content)

print("Deleting remote file...")
delete_file(BUCKET, REMOTE_PATH)

print("Done.")