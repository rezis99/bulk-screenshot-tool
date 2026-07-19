# Screenshot Tool (Streamlit)

Paste-in-URLs version of the Colab screenshot script. Two ways to use it:
run it locally on your own laptop, or deploy it to Streamlit Community Cloud
for a free, always-on public link (no laptop required, no credit card).

> Note: Hugging Face Spaces recently made Docker/Gradio Spaces paid-only
> (only their "Static" template is free, which can't run this app). Streamlit
> Community Cloud is the free option instead — and it's actually the more
> natural fit since this already is a Streamlit app.

---

## Option A — Run locally on your laptop (Windows)

### 1. One-time setup

Install Python from python.org (check "Add Python to PATH" during install).
Then in Command Prompt, in this folder:

```
pip install -r requirements.txt
playwright install chromium
```

### 2. Run

```
streamlit run app.py
```

Opens automatically at http://localhost:8501. Only works while your laptop
is on and the command is running.

---

## Option B — Free public link via Streamlit Community Cloud

This hosts the app on Streamlit's free servers. No laptop required — the
link works even when your computer is off.

### 1. Put the code on GitHub

Streamlit Community Cloud deploys directly from a GitHub repo, so the files
need to live there first.

1. Create a free account at **github.com** if you don't have one.
2. Create a **New repository** (e.g. `screenshot-tool`) — Public or Private
   both work.
3. Upload these files to it: `app.py`, `requirements.txt`, `packages.txt`,
   `README.md`. (GitHub's web interface has an "Add file → Upload files"
   button — no command-line git needed.)

### 2. Deploy on Streamlit Community Cloud

1. Go to **share.streamlit.io** and sign in with your GitHub account (free).
2. Click **New app**.
3. Pick your repository, branch (`main`), and set the main file path to
   `app.py`.
4. Click **Deploy**.

First deploy takes a few minutes — it installs the system libraries from
`packages.txt`, then Python packages from `requirements.txt`, then boots the
app. The very first page load will also trigger the one-time Chromium
browser download (this happens automatically inside `app.py` — no extra
step needed from you), which can add ~30–60 seconds to that first visit.

### 3. Set a password (important — do this before sharing the link)

1. On your app's page on Streamlit Cloud, click the **⋮** menu → **Settings**
   → **Secrets**
2. Add:
   ```
   APP_PASSWORD = "your-chosen-password"
   ```
3. Save — the app restarts automatically.

Now anyone opening your link is asked for that password before they can use
the tool. Share the password separately from the link (e.g. in your message
to them, not written on the page itself).

### 4. Share the link

Your app's public URL will look like:
```
https://YOUR-APP-NAME.streamlit.app
```
Send that link plus the password to whoever you want to try it.

### Free tier limits to know about

- Community Cloud apps go to sleep after a period of no traffic and take
  ~30–60 seconds to wake up on the next visit. Normal, not a bug.
- Free tier gives modest CPU/RAM — the app already caps concurrent tabs and
  batch size (see below) so it won't get overwhelmed.
- No credit card needed at any point for this tier.

---

## Built-in safety limits (already in app.py)

Since this becomes a public tool once shared, the app includes:

- **Password gate** — only shows the app after the correct password is
  entered (only active if you set the `APP_PASSWORD` secret; skipped
  automatically when running locally with no secret set)
- **Max 25 URLs per run** — prevents one person from overloading the server
- **Max 3 concurrent tabs** — keeps resource use modest on the free tier
- **Private/internal address blocking** — refuses to screenshot `localhost`,
  private IP ranges, or cloud metadata addresses, so the tool can't be used
  to probe internal networks

---

## Using the app

1. Sidebar: set Format (PNG/JPEG), viewport width, timeout, etc.
2. Main box: paste URLs, one per line. Optional custom filename after a comma:
   ```
   https://binaytara.org/research-grants
   https://binaytara.org/conferences/2025, conferences-2025
   ```
3. Click "▶ Run Screenshots"
4. Download the results as a zip (images + manifest.csv + errors.csv if any failed)
