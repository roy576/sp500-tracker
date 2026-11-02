# S&P 500 Daily Tracker: Setup Guide (about 20 minutes, all free)

You'll do 4 things: make a Google Sheet, create a Google "robot account" that's allowed to
write to it, put the code on GitHub, and give GitHub the robot's key.

---

## Step 1: Create the Google Sheet (2 min)
1. Go to https://sheets.new and name it **S&P 500 Daily Tracker**.
2. Copy the **Sheet ID** from the URL. It's the long part between `/d/` and `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit`
   Paste it into a note. You'll need it in Step 4.

## Step 2: Create a Google service account (robot account) (8 min)
This is the fiddliest step. Go slowly.
1. Go to https://console.cloud.google.com and sign in with the same Google account.
2. At the top, click the project dropdown → **New Project** → name it `sp500-tracker` → **Create**.
   Make sure the new project is selected in the dropdown.
3. In the search bar, type **Google Sheets API** → open it → **Enable**.
4. In the search bar, type **Service Accounts** → open it → **+ Create service account**.
   - Name: `tracker-bot` → **Create and continue** → skip the optional steps → **Done**.
5. Click the new service account → **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create**.
   A `.json` file downloads. **Keep it private**: it's a password for writing to your sheet.
6. Copy the service account's email (looks like `tracker-bot@sp500-tracker.iam.gserviceaccount.com`).
7. Go back to your Google Sheet → **Share** → paste that email → set it to **Editor** → uncheck "Notify" → **Share**.

## Step 3: Put the code on GitHub (5 min)
1. Create a free account at https://github.com/signup (skip if you have one).
2. Click **+** (top right) → **New repository** → name it `sp500-tracker` → choose **Private** → **Create repository**.
3. On the new repo page, click **uploading an existing file**.
4. Unzip `sp500-tracker.zip` on your computer and drag **everything inside it** into the upload box,
   including the `.github` folder.
   - If your computer hides the `.github` folder (Mac: press Cmd+Shift+. in Finder to show it), or the
     upload skips it, do this instead after uploading the other files: **Add file → Create new file**, type
     the name `.github/workflows/daily.yml`, paste in the contents of `daily.yml`, and commit.
5. Click **Commit changes**.

## Step 4: Give GitHub the secrets (3 min)
In your repo: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
Add two secrets:

| Name | Value |
|---|---|
| `GOOGLE_CREDENTIALS` | Open the downloaded `.json` key file in a text editor and paste **the whole contents** |
| `SHEET_ID` | The Sheet ID from Step 1 |

Then go to **Settings → Actions → General → Workflow permissions**, select
**Read and write permissions**, and click **Save**. This lets the script save its weekly share-count cache.

## Step 5: Test run (2 min)
1. Go to the **Actions** tab → click **Daily S&P 500 tracker** on the left → **Run workflow** → **Run workflow**.
2. Wait about 3–6 minutes. The first run is slower because it fetches share counts for all 500 stocks.
3. A green check means it worked. Open your Google Sheet: there's a new tab named with the latest
   trading date.
4. A red X means it failed. Click into it, copy the error text, and send it to me.

That's it. From now on it runs automatically **every weekday at 2:15 PM Pacific** (3:15 PM during
daylight saving time) and skips market holidays. GitHub emails you if a run fails.

---

## Reading the sheet
- **Row 2** shows the index move and either **CHECKS PASSED** or **NEEDS REVIEW** with the reason
  (missing prices, a mismatch against SPY's real move, suspicious >50% moves).
- Rows are grouped **SECTOR → SUB-INDUSTRY → STOCK**, each sorted best to worst.
- **% Change** for sectors and sub-industries is cap-weighted, like the index itself.
- **Mkt Cap Change ($B)** is the group's dollar move in total market value.
- **Contribution to S&P (pts)** is how many percentage points the group added to or subtracted from
  the index. All sectors add up to the index move.
- **Why it moved** says `NEWS NEEDED` for every stock that moved ±3% or more. The AI news step
  replaces it with up to 3 sourced bullets.

## Changing things later
- **Threshold:** add `MOVE_THRESHOLD: "4"` under `env:` in `daily.yml`.
- **Run time:** edit the `cron:` line in `daily.yml` (times are in UTC).
