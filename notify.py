"""
Monitors a Google Drive folder for document updates and sends Gmail notifications
with an AI-generated summary of the document content.
"""

import io
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import yaml
from google.cloud import storage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
STATE_FILE = "last_seen_state.json"

EXPORTABLE_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
DOWNLOADABLE_TYPES = {"text/plain", "text/markdown", "text/csv"}


def _credentials():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    info = json.loads(raw) if raw else json.load(open("service_account.json"))
    return service_account.Credentials.from_service_account_info(info, scopes=DRIVE_SCOPES)


def get_drive_service():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def list_folder_files(service, folder_id: str) -> list[dict]:
    return (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id,name,modifiedTime,webViewLink,mimeType)",
            orderBy="modifiedTime desc",
        )
        .execute()
        .get("files", [])
    )


def read_file_text(service, file_id: str, mime_type: str, limit: int) -> str:
    try:
        if mime_type in EXPORTABLE_TYPES:
            data = service.files().export(
                fileId=file_id, mimeType=EXPORTABLE_TYPES[mime_type]
            ).execute()
            text = data.decode("utf-8") if isinstance(data, bytes) else data
        elif mime_type in DOWNLOADABLE_TYPES:
            request = service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            text = buf.getvalue().decode("utf-8", errors="ignore")
        else:
            return f"[File type '{mime_type}' is not supported for content extraction]"
    except Exception as exc:
        return f"[Could not read file content: {exc}]"
    return text[:limit]


def load_state() -> dict:
    bucket_name = os.environ.get("STATE_BUCKET")
    if bucket_name:
        blob = storage.Client().bucket(bucket_name).blob(STATE_FILE)
        return json.loads(blob.download_as_text()) if blob.exists() else {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as fh:
            return json.load(fh)
    return {}


def save_state(state: dict) -> None:
    bucket_name = os.environ.get("STATE_BUCKET")
    if bucket_name:
        storage.Client().bucket(bucket_name).blob(STATE_FILE).upload_from_string(
            json.dumps(state)
        )
    else:
        with open(STATE_FILE, "w") as fh:
            json.dump(state, fh)


def generate_summary(file_name: str, content: str, model: str) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": (
                    f"The document '{file_name}' was recently updated. "
                    f"Here is its current content:\n\n{content}\n\n"
                    "Write a concise 2-4 sentence summary of what this document "
                    "contains and what key information it covers."
                ),
            }
        ],
    )
    return response.content[0].text


def send_notification(recipients: list[str], file_name: str, file_link: str, summary: str) -> None:
    smtp_user = os.environ["GMAIL_USER"]
    smtp_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Document Updated: {file_name}"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)

    plain = (
        f"A document in your monitored Google Drive folder has been updated.\n\n"
        f"Document: {file_name}\n"
        f"Link:     {file_link}\n\n"
        f"Summary\n-------\n{summary}\n\n"
        f"---\nISO Optimization automated notification"
    )
    html = f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333;">
  <p>A document in your monitored Google Drive folder has been updated.</p>
  <p>
    <strong>Document:</strong>
    <a href="{file_link}">{file_name}</a>
  </p>
  <h3 style="margin-bottom: 4px;">Summary</h3>
  <p style="margin-top: 0;">{summary}</p>
  <hr style="border: none; border-top: 1px solid #ddd;">
  <p><small>ISO Optimization automated notification</small></p>
</body>
</html>
"""
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipients, msg.as_string())


def main() -> None:
    with open("config.yaml") as fh:
        config = yaml.safe_load(fh)

    folder_id: str = config["folder_id"]
    recipients: list[str] = config["recipients"]
    model: str = config.get("summary_model", "claude-sonnet-4-6")
    content_limit: int = config.get("summary_content_limit", 8000)

    service = get_drive_service()
    state = load_state()
    new_state = dict(state)

    files = list_folder_files(service, folder_id)
    notified = 0

    for file in files:
        fid = file["id"]
        modified = file["modifiedTime"]

        if fid not in state:
            new_state[fid] = modified
            continue

        if state[fid] == modified:
            continue

        print(f"Updated: {file['name']}")
        new_state[fid] = modified

        content = read_file_text(service, fid, file["mimeType"], content_limit)
        summary = generate_summary(file["name"], content, model)
        send_notification(recipients, file["name"], file["webViewLink"], summary)
        print(f"  → notification sent to {recipients}")
        notified += 1

    save_state(new_state)
    print(f"Done. Scanned {len(files)} file(s), sent {notified} notification(s).")


if __name__ == "__main__":
    main()
