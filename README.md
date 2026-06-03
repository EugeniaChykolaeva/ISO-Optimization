# Setup Guide

## Prerequisites

- Google Cloud project: `iso-optimization`
- Service account: `ios-optimization@iso-optimization.iam.gserviceaccount.com`
- A Gmail App Password for the sending account

---

## Step 1 — Share the Drive folder with the service account

Open the monitored folder in Google Drive and share it with:
ios-optimization@iso-optimization.iam.gserviceaccount.com
Grant **Viewer** access (read-only is sufficient).

---

## Step 2 — Enable required Google APIs

In the [Google Cloud Console](https://console.cloud.google.com) for project `iso-optimization`:

1. Enable **Google Drive API**
2. Enable **Cloud Storage API** (for state persistence)
3. Enable **Cloud Run API**
4. Enable **Cloud Scheduler API**

---

## Step 3 — Create a Cloud Storage bucket for state

`gsutil mb -p iso-optimization gs://iso-optimization-state`

Grant the service account access:

`gsutil iam ch serviceAccount:ios-optimization@iso-optimization.iam.gserviceaccount.com:roles/storage.objectAdmin \
  gs://iso-optimization-state`

---

## Step 4 — Store secrets in Secret Manager

# Service account key
`gcloud secrets create service-account-json \
  --data-file=service_account.json \
  --project=iso-optimization`

# Anthropic API key
`echo -n "YOUR_ANTHROPIC_API_KEY" | \
  gcloud secrets create anthropic-api-key --data-file=- --project=iso-optimization`

# Gmail credentials
`echo -n "your-email@gmail.com" | \
  gcloud secrets create gmail-user --data-file=- --project=iso-optimization`

echo -n "YOUR_GMAIL_APP_PASSWORD" | \
  `gcloud secrets create gmail-app-password --data-file=- --project=iso-optimization`

Gmail App Password: Go to https://myaccount.google.com/apppasswords and generate a password for "Mail".

Grant the service account access to the secrets:

for SECRET in service-account-json anthropic-api-key gmail-user gmail-app-password; do
  gcloud secrets add-iam-policy-binding $SECRET \
   ` --member="serviceAccount:ios-optimization@iso-optimization.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project=iso-optimization`
done

---

## Step 5 — Build and push the container

gcloud builds submit \
 ` --tag gcr.io/iso-optimization/drive-notifier \
  --project=iso-optimization`

---

## Step 6 — Deploy as a Cloud Run Job

gcloud run jobs create drive-notifier \
  `--image gcr.io/iso-optimization/drive-notifier \
  --region europe-west1 \
  --service-account ios-optimization@iso-optimization.iam.gserviceaccount.com \
  --set-env-vars STATE_BUCKET=iso-optimization-state \
  --set-secrets GOOGLE_SERVICE_ACCOUNT_JSON=service-account-json:latest \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --set-secrets GMAIL_USER=gmail-user:latest \
  --set-secrets GMAIL_APP_PASSWORD=gmail-app-password:latest \
  --project=iso-optimization`

---

## Step 7 — Schedule with Cloud Scheduler

Run once a month on the first Monday of the month at 09:00 UTC:

gcloud scheduler jobs create http drive-notifier-schedule \
  `--schedule="0 9 1-7 * 1" \
  --uri="https://REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/iso-optimization/jobs/drive-notifier:run" \
  --http-method=POST \
  --oauth-service-account-email=ios-optimization@iso-optimization.iam.gserviceaccount.com \
  --location=europe-west1 \
  --project=iso-optimization`

---

## Adding more recipients

Edit config.yaml:

recipients:
  - "echykolaeva@waverleysoftware.com"
  - "another.person@example.com"

Then rebuild and redeploy the container (Steps 5–6).

---

## Local testing

`pip install -r requirements.txt
cp /path/to/service_account.json .
export ANTHROPIC_API_KEY=sk-ant-...
export GMAIL_USER=you@gmail.com
export GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
python notify.py`




