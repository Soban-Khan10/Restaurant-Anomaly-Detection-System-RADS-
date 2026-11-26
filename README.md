# Restaurant-Anomaly-Detection-System-RADS-
AI-powered food-safety monitoring for restaurant kitchens using CCTV. A YOLO26-based detector combined with a configurable rule-based engine identifies hygiene and safety violations in real time and logs them as reviewable incidents with visual evidence. Designed to run on local hardware.

## Running the app server and dashboard

RADS runs as three programs on one computer:

| Program | Address | What it does |
|---|---|---|
| App server (`app/`, FastAPI) | http://127.0.0.1:8000 | Database, sign-in, incidents, alerts, reports; API docs at `/docs` |
| Dashboard (`dashboard/`, React) | http://localhost:5173 | The web pages people use |
| Camera system / worker (`worker/`) | http://127.0.0.1:8001 | Watches the cameras and records incidents (set up separately) |

The steps below start the app server and the dashboard. Run every command from the repository root unless a step
says otherwise. Commands are given for macOS (Terminal, zsh) and Windows (PowerShell).

### 1. Prerequisites

- **Python 3.12** – check with `python3.12 --version` (macOS) or `py -3.12 --version` (Windows).
- **Node.js 20 or newer** (includes npm) – check with `node --version`.
- Git, and a copy of this repository on the `app` branch (or `main` once it is merged).

### 2. Python virtual environment and app packages

macOS:
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install sqlalchemy "pydantic>=2.6" pyyaml python-dotenv "bcrypt>=4.1,<5" tzdata httpx fastapi "uvicorn[standard]" "python-jose[cryptography]" "passlib[bcrypt]" reportlab openpyxl "apscheduler>=3.10,<4" python-multipart pytest
```

Windows:
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install sqlalchemy "pydantic>=2.6" pyyaml python-dotenv "bcrypt>=4.1,<5" tzdata httpx fastapi "uvicorn[standard]" "python-jose[cryptography]" "passlib[bcrypt]" reportlab openpyxl "apscheduler>=3.10,<4" python-multipart pytest
```

These are the "shared", "app server" and "tests" packages from `requirements.txt`. `pip install -r requirements.txt`
also works but installs the worker's packages too (they need PyTorch installed first). Activate the virtual
environment again in every new terminal (`source .venv/bin/activate` or `.\.venv\Scripts\Activate.ps1`).

### 3. Settings (`.env`)

macOS:
```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice: one value for each secret below
```

Windows:
```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice: one value for each secret below
```

Then edit `.env`. The app server uses these values:

| Setting | What to put |
|---|---|
| `RADS_JWT_SECRET` | A long random value (from the command above). Signs sign-in tokens; changing it signs everyone out. |
| `RADS_INTERNAL_TOKEN` | Another long random value. The worker sends it with every call to the app, so the worker must use **exactly the same value**. |
| `RADS_DATA_DIR`, `RADS_DB_PATH` | Where the data folder and database live. Defaults: `./rads_data` and `./rads_data/rads.db`. |
| `RADS_API_URL` | The app server's address, used by the worker. Default `http://127.0.0.1:8000`. |
| `RADS_WORKER_URL` | The worker's address, used by the app for live view and "reload settings". Default `http://127.0.0.1:8001`. |
| `CAM1_RTSP`, `CAM2_RTSP` | The cameras' RTSP addresses, including user and password. Copied into the database by the seed step. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` | Optional: WhatsApp alerts (Meta Cloud API). Leave empty to use dashboard alerts only. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional: Telegram alerts to a chat or group. `TELEGRAM_CHAT_ID` is not in `.env.example`; add the line if you use Telegram (several ids may be separated by commas). |

`RADS_MODEL_PATH`, `RADS_IMGSZ` and `RADS_DEVICE` are used only by the worker. Never commit `.env`: it holds the
secrets and the camera passwords.

### 4. Database: create, seed, set the camera picture size

macOS:
```bash
python scripts/init_db.py
python scripts/seed.py          # asks for the password of the "admin" user (at least 8 characters)
python - <<'EOF'
import sqlite3
from shared.config import get_settings
db = get_settings().db_path
with sqlite3.connect(db) as c:
    n = c.execute("UPDATE cameras SET frame_width = 2688, frame_height = 1520 WHERE frame_width IS NULL").rowcount
print(f"{db}: picture size 2688 x 1520 set on {n} camera(s)")
EOF
```

Windows:
```powershell
python scripts\init_db.py
python scripts\seed.py          # asks for the password of the "admin" user (at least 8 characters)
@'
import sqlite3
from shared.config import get_settings
db = get_settings().db_path
with sqlite3.connect(db) as c:
    n = c.execute("UPDATE cameras SET frame_width = 2688, frame_height = 1520 WHERE frame_width IS NULL").rowcount
print(f"{db}: picture size 2688 x 1520 set on {n} camera(s)")
'@ | python -
```

- `init_db.py` creates the empty database. It refuses to overwrite an existing one; `--force` deletes it and starts
  again, **including all incidents**.
- `seed.py` adds the roles, the admin user, the hazards, both cameras (from `CAM1_RTSP` / `CAM2_RTSP`), the areas
  and the detection rules from `config/defaults.yaml`. It is safe to run again: existing rows are kept.
- The last command sets the cameras' picture size (both cameras are 2688 × 1520). Area outlines are stored in these
  pixels, and the area editor needs the size to work. It changes only cameras without a size, so it is safe to run
  again. Instead of this command, you can also set the size in the dashboard: **Cameras & areas → Edit**.

### 5. Start the app server

macOS / Windows (virtual environment active):
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave this terminal open; stop the server with Ctrl+C. Check http://127.0.0.1:8000/docs — the API documentation
should open. Without cameras or the worker you can still try everything with test data: in a second terminal (venv
active) run `python scripts/fake_incidents.py`, which creates a fake incident every 20 seconds and stands in for the
worker's live view.

### 6. Start the dashboard

In a new terminal:

macOS:
```bash
cd dashboard && npm install && npm run dev
```

Windows (Windows PowerShell 5.1 does not support `&&`):
```powershell
cd dashboard
npm install
npm run dev
```

`npm install` is needed only the first time and after the dashboard's packages change. Leave the terminal open;
stop the dashboard with Ctrl+C.

### 7. Open the dashboard and sign in

Open **http://localhost:5173** and sign in as `admin` with the password you chose in step 4. Add the other users
(managers, technical staff) under **Users**. The browser plays the new-incident chime only after you have clicked
somewhere on the page once; the header shows "Click to allow sound" until then.

### 8. The dashboard's own `.env` (API address)

The dashboard calls the app server at `http://127.0.0.1:8000` by default. Only if the server runs somewhere else:

macOS:
```bash
cp dashboard/.env.example dashboard/.env      # then edit VITE_API_URL in dashboard/.env
```

Windows:
```powershell
Copy-Item dashboard\.env.example dashboard\.env   # then edit VITE_API_URL in dashboard\.env
```

Restart `npm run dev` after changing it. The dashboard always runs on port 5173, because the app server only accepts
requests from a dashboard at `http://localhost:5173` or `http://127.0.0.1:5173`.

### Troubleshooting

- **Port already in use.** The server stops with `[Errno 48] Address already in use` (macOS) or `[Errno 10048] ...
  only one usage of each socket address` (Windows), or the dashboard with `Port 5173 is already in use`. Another copy
  is still running. Find it and stop it, rather than moving to another port:
  - macOS: `lsof -i :8000` (or `:5173`), then `kill <PID>`.
  - Windows: `netstat -ano | findstr :8000` (or `:5173`); the last number is the PID; then `Stop-Process -Id <PID>`.
- **The worker's calls get 401 (in the app server's log: `POST /internal/heartbeat ... 401`).** The worker and the
  app server use different `RADS_INTERNAL_TOKEN` values, or one of them is empty. Put the same value in both `.env`
  files (or use one shared `.env`) and restart both. A 403 instead means the call did not come from this computer.
- **Live view says "Camera system offline", cameras show Offline.** The worker is not running or not reachable at
  `RADS_WORKER_URL`: the live view and snapshot return 503, and cameras turn Offline after 15 seconds without a
  heartbeat. Start the worker (or `scripts/fake_incidents.py`). Settings saved meanwhile are kept; the dashboard says
  "Saved, but the camera system is offline; it will use the new values when it restarts".
- **The dashboard says "Cannot reach the RADS server".** The app server is not running, or `VITE_API_URL` in
  `dashboard/.env` points to the wrong address.
- **Windows: `Activate.ps1 cannot be loaded because running scripts is disabled`.** Run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

## Email alerts

RADS can email staff when it opens an incident. The alert always appears on the dashboard first; the email is an
extra copy for someone who is not looking at the screen.

- **New incident** → every active **Manager** who has an email address.
- **Not reviewed for 10 minutes** (still New) → every active **Admin** who has an email address.
- Each incident sends at most one new-incident email and one escalation email to each person, even if the app server
  restarts. Incidents that start while monitoring is paused send no email.
- Emails go out only for incidents that started within the last 60 minutes. After the app server or the computer
  was off (or asleep), older incidents still get their dashboard alert and escalation, but no email, so nobody's
  inbox is flooded with old incidents.
- Technical users get no alert emails.

Telegram is also supported (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), but Telegram is not available in Pakistan,
so leave those settings empty. WhatsApp (`WHATSAPP_TOKEN`) is optional and also off by default.

### 1. Create a Gmail app password

Use a Gmail account just for RADS (for example `rads.alerts.yourrestaurant@gmail.com`), not a personal one.

1. Sign in to that account and turn on **2-Step Verification** at <https://myaccount.google.com/security>. App
   passwords only exist once this is on.
2. Open <https://myaccount.google.com/apppasswords>, type a name such as `RADS` and click **Create**.
3. Google shows a 16-letter password such as `abcd efgh ijkl mnop`. Copy it into `.env` (next step). You can keep
   or drop the spaces. Google shows it only once; if you lose it, delete it and create a new one.

The app password is not your Gmail password. If it leaks, delete it on the same page; the Gmail account stays safe.

### 2. Settings in `.env`

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=rads.alerts.yourrestaurant@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop
SMTP_FROM=RADS Alerts <rads.alerts.yourrestaurant@gmail.com>
SMTP_STARTTLS=true
# Optional: adds an "Open incident" button to each email. Use the address staff open on their phones.
RADS_DASHBOARD_URL=http://192.168.1.10:5173
# Optional: attach the snapshot to each email (see the privacy note below).
EMAIL_ATTACH_SNAPSHOT=false
```

Email is on only when `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM` are all set; otherwise RADS sends
no email and shows no error. Put comments on their own lines, not after a value. Restart the app server after any
change. The server log then shows `alert channels: dashboard, email`.

Then give people their address: **Users** → **Edit** → **Email**. The list shows **gets email alerts** next to every
active Manager and Admin who will get them.

RADS never prints, logs or stores the password; it is filtered out of every log line. Keep `.env` out of git (it is
already in `.gitignore`).

### 3. Test it

On the **Users** page (Admins only), the **Alerts** card shows whether email is on, the Gmail account and server
being used (never the password), how many Managers and Admins will get emails, and the result of the last email
alert. Click **Send test email**: it goes to your own email address, or to the address typed in the box. Within a
minute you should get **"RADS test email – alerts are working"**. If it fails, the card says why in plain words.

Every test is recorded in the audit log. The **Alerts sent** list on each incident shows one row per email, with the
person's name and Sent or Failed.

### 4. Troubleshooting

| What the card or incident says | What to do |
| --- | --- |
| Email alerts are off. Set SMTP_HOST, ... in the .env file | Fill in the four settings, then restart the app server. |
| Gmail rejected the login – check SMTP_USER and the app password | `SMTP_USER` must be the full Gmail address, and `SMTP_PASSWORD` an app password (not the Gmail password). Create a new one if unsure. 2-Step Verification must be on. |
| Could not reach the mail server smtp.gmail.com:587 (timed out) – tried 3 times | No internet, or the network blocks port 587. Check the connection; try another network. RADS retries 3 times. |
| Secure connection (STARTTLS) ... failed | Use `SMTP_PORT=587` with `SMTP_STARTTLS=true`. |
| The mail server refused the address ... (error 550) | The recipient's email address is wrong. Fix it on the Users page. |
| no recipients with an email address | No active Manager (or, for escalations, Admin) has an email. Add one on the Users page. |
| The test says Sent but nothing arrives | Look in the Spam folder and mark it "Not spam". Check the address on the Users page. |

Gmail allows about 500 emails a day from a normal account, far more than RADS sends.

### Privacy: `EMAIL_ATTACH_SNAPSHOT`

Snapshots show kitchen staff. With `EMAIL_ATTACH_SNAPSHOT=false` (the default) emails contain only text and a link,
and the picture stays on the RADS computer, where only signed-in users can see it and every view is logged. With
`true`, each email carries the snapshot: it is then copied to Google's servers and to every recipient's phone, and
RADS can no longer delete it or log who sees it. Turn it on only if the restaurant and its staff have agreed to that.
Every email ends with: "Automatic alert from RADS. Detections can be wrong; a person must review every incident."
