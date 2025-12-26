# README — Video Theme Matcher (Google Sheet + Gemini)

## What this project does
This script automatically:
1. Reads a Google Sheet containing **Google Drive video links** and **themes**
2. For each unprocessed row (in small batches):
   - Downloads the video from Google Drive
   - Extracts frames from the video (1 frame per second)
   - Selects up to 10 frames evenly across the video
   - Resizes frames (to reduce payload size)
   - Sends all selected frames together to **Gemini (multimodal)** with the theme
   - Gets back **only JSON**: `{ match_score, feedback }`
3. Writes the result back into the same Google Sheet:
   - `match_score` (0–100)
   - `feedback` (1–2 lines)
   - `status` (PROCESSING / DONE / ERROR)
   - `processed_at` timestamp (UTC)

The script stores the video + frames in a **temporary folder** and deletes them automatically after processing each row.

---

## Google Sheet format (required)
Create a sheet with these columns (Row 1 headers recommended):

| Column | Name          | Purpose |
|-------:|---------------|---------|
| A      | drive_link    | Google Drive video share link |
| B      | theme         | Theme to match against |
| C      | match_score   | Output (written by script) |
| D      | feedback      | Output (written by script) |
| E      | status        | PROCESSING / DONE / ERROR |
| F      | processed_at  | UTC timestamp |

The script reads `A:B` and writes into `C:F`.

---

## How batching works
- The script processes only `BATCH_SIZE` rows per run (example: 5).
- It processes only rows where:
  - `drive_link` is present (Column A)
  - `theme` is present (Column B)
  - `match_score` is empty (Column C)

This lets you run it repeatedly (cron / scheduler) until all rows are processed.

---

## Requirements
### Python packages
Install:
```bash
pip install opencv-python gdown google-genai google-auth google-api-python-client


Gemini API Key

Put your key into:

GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"


Model used:

MODEL_NAME = "gemini-2.5-flash"

Google Sheets access (Service Account)

This script uses a Google Cloud Service Account to read/write your sheet.

Steps:

Create a Service Account in Google Cloud Console

Download the JSON key file → save as:

credentials.json


Share your Google Sheet with the service account email (Editor access)

Set:

SPREADSHEET_ID = "YOUR_SHEET_ID"
SHEET_NAME = "Sheet1"
SERVICE_ACCOUNT_JSON = "credentials.json"