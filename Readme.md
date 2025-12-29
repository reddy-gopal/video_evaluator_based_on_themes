# README — Video Theme Matcher (Google Sheet + Gemini)

## What this project does
This script automatically:
1. Reads a Google Sheet containing **Google Drive video links** and **Ids**
2. For each row (in small batches):
   - Downloads the video from Google Drive
   - Extracts frames from the video (1 frame per second)
   - Selects up to 10 frames evenly across the video
   - Resizes frames (to reduce payload size)
   - Sends all selected frames together to **Gemini (multimodal)** for theme matching
   - Gets back **JSON** with match scores and evaluation criteria
3. Writes the result back into the same Google Sheet:
   - `best_theme` (the matched theme)
   - `match_score` (0–100)
   - `feedback` (1–2 lines)
   - `relevance_score`, `visual_quality_score`, `creativity_score`, `technical_execution_score`, `engagement_potential_score` (0–100 each)
   - `status` (PROCESSING / DONE / ERROR)
   - `processed_at` timestamp (UTC)

The script stores the video + frames in a **temporary folder** and deletes them automatically after processing each row.

---

## Prerequisites

### 1. Python Packages
Install required packages:
```bash
pip install opencv-python google-genai google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

**Note:** We removed `gdown` since we're now using Google Drive API directly with OAuth authentication.

### 2. Gemini API Key
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key
3. Copy the API key

### 3. Google Cloud OAuth Setup

This script uses **OAuth user authentication** (not service account), which means it uses your own Gmail account to access files. This allows you to access any Drive files that your Gmail account has permission to view, without needing to share them with a service account.

#### Step 1: Create a Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click on the project dropdown at the top
3. Click **"New Project"**
4. Enter a project name (e.g., "Video Theme Matcher")
5. Click **"Create"**
6. Select your newly created project

#### Step 2: Enable Required APIs
1. In the Google Cloud Console, go to **"APIs & Services"** > **"Library"**
2. Search for and enable these APIs:
   - **Google Sheets API** (required)
   - **Google Drive API** (required - needed to download videos)

#### Step 3: Configure OAuth Consent Screen
1. Go to **"APIs & Services"** > **"OAuth consent screen"**
2. Select **"External"** (or **"Internal"** if you're using Google Workspace)
3. Click **"Create"**
4. Fill in the required information:
   - **App name**: Video Theme Matcher (or any name)
   - **User support email**: Your email address
   - **Developer contact information**: Your email address
5. Click **"Save and Continue"**
6. On the **Scopes** page, click **"Add or Remove Scopes"**
7. Add these scopes:
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/drive.readonly`
8. Click **"Update"** then **"Save and Continue"**
9. On the **Test users** page (if app is in testing mode):
   - Click **"+ ADD USERS"**
   - Add your Gmail address (the one you'll use to sign in)
   - Click **"Add"**
10. Click **"Save and Continue"** then **"Back to Dashboard"**

#### Step 4: Create OAuth Client ID
1. Go to **"APIs & Services"** > **"Credentials"**
2. Click **"+ CREATE CREDENTIALS"** at the top
3. Select **"OAuth client ID"**
4. If prompted, select **"Desktop app"** as the application type
5. Enter a name (e.g., "Video Theme Matcher Client")
6. Click **"Create"**
7. A dialog will appear with your Client ID and Client Secret
8. Click **"Download JSON"** (or copy the credentials)
9. **Rename the downloaded file to `client_secret.json`**
10. Place `client_secret.json` in the project root directory (same folder as `index.py`)

**Important Notes:**
- The first time you run the script, a browser window will open asking you to sign in with your Google account
- Sign in with the **Gmail account that has access to the Drive files** you want to process
- After authorization, a `token.json` file will be created (automatically saved)
- The token will be refreshed automatically when it expires
- If you need to re-authorize, delete `token.json` and run the script again

---

## Google Sheet Setup

### Step 1: Create Your Google Sheet
1. Go to [Google Sheets](https://sheets.google.com/)
2. Create a new spreadsheet
3. Set up the headers in **Row 1** (case-insensitive, spaces are converted to underscores):

| Column Header | Required | Description |
|---------------|----------|-------------|
| `Id` | ✅ Yes | Unique identifier for each row |
| `Drive Link` or `drive_link` | ✅ Yes | Google Drive share link to the video |
| `match_score` | ✅ Yes | Output column (will be filled by script) |
| `feedback` | ✅ Yes | Output column (will be filled by script) |
| `status` | ✅ Yes | Output column (PROCESSING/DONE/ERROR) |
| `processed_at` | ✅ Yes | Output column (timestamp) |
| `best_theme` or `theme` | ⚪ Optional | Output column (matched theme) |
| `relevance_score` | ⚪ Optional | Output column (evaluation criteria) |
| `visual_quality_score` | ⚪ Optional | Output column (evaluation criteria) |
| `creativity_score` | ⚪ Optional | Output column (evaluation criteria) |
| `technical_execution_score` | ⚪ Optional | Output column (evaluation criteria) |
| `engagement_potential_score` | ⚪ Optional | Output column (evaluation criteria) |

### Step 2: Ensure Sheet Access
1. Make sure your Google Sheet is accessible to the Gmail account you'll use to sign in
2. If the sheet is owned by someone else, ask them to share it with your Gmail account with **"Editor"** permission
3. If you own the sheet, you're all set!

### Step 3: Add Your Data
1. Fill in the `Id` column with unique identifiers (numbers or text)
2. Fill in the `Drive Link` column with Google Drive share links
3. Leave output columns empty (they will be filled by the script)
4. **Note:** Rows with empty drive links will be skipped

---

## Google Drive Video Setup

### Making Videos Accessible
**IMPORTANT:** The script uses OAuth authentication with your Gmail account. This means:
- ✅ You can access any Drive files that your Gmail account has permission to view
- ✅ No need to share files with a service account
- ✅ Files can remain private ( account)
- ✅ Works with files owned by you or shared with you
only shared with your Gmail
#### How It Works
1. When you first run the script, a browser window will open
2. Sign in with your **Gmail account** (the one that has access to the Drive files)
3. Grant permissions for Sheets and Drive access
4. The script will remember your authorization (saved in `token.json`)
5. You can access any files that this Gmail account can see

#### For Student Submissions
If students have uploaded videos to Google Drive:
- **Option 1:** Have students share their video files with your Gmail account
- **Option 2:** Have students upload to a shared folder that you have access to
- **Option 3:** If you're a teacher/admin, you may already have access to student files

**No special setup needed** - as long as your Gmail account can see the files, the script can download them!

---

## Project Configuration

### Step 1: Update API Key
Edit `index.py` and update:
```python
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"
```

### Step 2: Verify OAuth Files
Ensure `client_secret.json` is in the project root directory:
```
Task6/
├── index.py
├── client_secret.json  ← OAuth client secrets (download from Google Cloud Console)
├── token.json          ← Auto-generated after first authorization (don't create manually)
├── Readme.md
└── .gitignore
```

**Note:** `token.json` will be created automatically after the first successful OAuth authorization. You don't need to create it manually.

### Step 3: Configure Settings (Optional)
You can adjust these settings in `index.py`:
```python
BATCH_SIZE = 5  # Number of rows to process per run
EXTRACT_EVERY_SEC = 1.0  # Extract 1 frame per second
MAX_FRAMES_TO_SEND = 10  # Maximum frames to send to Gemini
RESIZE_MAX_SIDE = 768  # Resize images to reduce payload
```

---

## Running the Script

### Basic Usage
```bash
python index.py
```

When prompted, enter your Google Sheet URL:
```
Enter the Google Sheet URL: https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
```

### How It Works
1. The script reads all rows from your sheet
2. For each row with an `Id` and non-empty `Drive Link`:
   - Sets status to `PROCESSING`
   - Downloads the video
   - Extracts and analyzes frames
   - Writes results back to the sheet
   - Sets status to `DONE` or `ERROR`
3. Processes up to `BATCH_SIZE` rows per run
4. You can run it multiple times to process more rows

### Example Output
```
DONE | Id=1 | theme=Transform famous brand logos... | score=85
DONE | Id=2 | theme=Create hilarious animations... | score=72
ERROR | Id=3 | Google Drive download failed...
Processed 3 row(s).
```

---

## Troubleshooting

### Error: "Missing required headers"
- **Solution:** Ensure your sheet has all required headers in Row 1: `Id`, `Drive Link`, `match_score`, `feedback`, `status`, `processed_at`

### Error: "File not found (404)" or "Cannot access file"
This means the Gmail account you signed in with cannot see the files.

**Solutions:**
1. **Verify you signed in with the correct Gmail account** - the one that has access to the Drive files
2. **Check file permissions** - make sure the files are shared with your Gmail account
3. **Re-authorize if needed:**
   - Delete `token.json`
   - Run the script again
   - Sign in with the correct Gmail account
4. **Verify the Drive link is correct** - check that the file ID in the URL is valid

### Error: "OAuth client secrets file not found"
- **Solution:** Follow Step 4 in the OAuth Setup section above to download `client_secret.json` from Google Cloud Console

### Error: "OAuth consent denied" or "invalid_client"
- **Solution:**
  1. Delete `token.json` if it exists
  2. Verify `client_secret.json` is the correct file downloaded from Google Cloud Console
  3. Make sure your email is added as a test user in OAuth consent screen (if app is in testing mode)
  4. Run the script again

### Error: "Invalid Google Sheets URL"
- **Solution:** Use the full URL from your browser's address bar when viewing the sheet

### Error: "Permission denied" or "Access denied"
- **Solution:** 
  - Verify the Gmail account you signed in with has Editor access to the sheet
  - Check that your Gmail account has access to the video files
  - Ensure `client_secret.json` is correct and in the right location
  - Try deleting `token.json` and re-authorizing

### Error: "No frames extracted from video"
- **Solution:** 
  - Check that the video file is valid and not corrupted
  - Verify the video has a valid frame rate
  - Try a different video file

### Rows Not Processing
- **Solution:** 
  - Ensure rows have an `Id` value
  - Ensure rows have a non-empty `Drive Link`
  - Check that the `status` column is empty or doesn't have "DONE" (script will update existing rows)

---

## File Structure

```
Task6/
├── index.py              # Main script
├── client_secret.json    # OAuth client secrets (DO NOT COMMIT)
├── token.json           # OAuth token (auto-generated, DO NOT COMMIT)
├── Readme.md            # This file
└── .gitignore           # Should include client_secret.json and token.json
```

---

## Security Notes

⚠️ **IMPORTANT:**
- Never commit `client_secret.json` or `token.json` to version control
- Keep your Gemini API key secure
- The OAuth token (`token.json`) contains access to your Google account - keep it secure
- If `token.json` is compromised, revoke access in Google Account settings and delete the file
- Regularly rotate API keys if compromised

---

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Verify all setup steps were completed correctly
3. Check that your Google Sheet format matches the requirements
4. Ensure all API keys and credentials are valid
