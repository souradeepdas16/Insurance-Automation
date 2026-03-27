# Insurance Automation — Ubuntu Server Setup

## Prerequisites

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
```

Verify Python ≥ 3.10:

```bash
python3 --version
```

---

## Step 1 — Transfer / Clone the Project

**Option A — SCP from Windows:**

```bash
scp -r "D:\Projects_S\Insurance Automation" user@your-server-ip:/home/user/insurance-automation
```

**Option B — Git:**

```bash
git clone <your-repo-url> /home/user/insurance-automation
cd /home/user/insurance-automation
```

---

## Step 2 — Create & Activate Virtual Environment

```bash
cd /home/user/insurance-automation
python3 -m venv venv
source venv/bin/activate
```

---

## Step 3 — Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 4 — Configure Environment Variables

```bash
cp .env.example .env
nano .env
```

Set **one** of the following providers:

```env
# Option A — OpenRouter (paid)
# Get key at: https://openrouter.ai/keys
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
AI_MODEL=openai/gpt-5.4-pro

# Option B — Google AI Studio (free)
# Get key at: https://aistudio.google.com/apikey
AI_PROVIDER=google
GOOGLE_API_KEY=your-key-here
AI_MODEL=gemini-2.5-flash
```

---

## Step 5 — Open Firewall Port

```bash
sudo ufw allow 8000
sudo ufw enable
```

---

## Step 6 — Run the Server

**Development (foreground):**

```bash
python3 server.py
```

Access at: `http://your-server-ip:8000`

---

## Step 7 — Run as a Systemd Service (Production)

Create the service file:

```bash
sudo nano /etc/systemd/system/insurance-automation.service
```

Paste the following (replace `your-linux-username` and path as needed):

```ini
[Unit]
Description=Insurance Automation Server
After=network.target

[Service]
User=your-linux-username
WorkingDirectory=/home/user/insurance-automation
ExecStart=/home/user/insurance-automation/venv/bin/python server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable insurance-automation
sudo systemctl start insurance-automation
sudo systemctl status insurance-automation
```

View live logs:

```bash
journalctl -u insurance-automation -f
```

---

## Data Locations (auto-created on first run)

| Path                | Purpose                               |
| ------------------- | ------------------------------------- |
| `data/insurance.db` | SQLite database (cases, settings)     |
| `cases/`            | Per-case uploaded documents & outputs |
| `logs/`             | AI raw extraction logs                |

---

## Troubleshooting

| Issue                       | Fix                                                           |
| --------------------------- | ------------------------------------------------------------- |
| `ModuleNotFoundError`       | Re-run `pip install -r requirements.txt` with venv active     |
| `Permission denied` on venv | `chmod +x venv/bin/activate`                                  |
| Port 8000 blocked           | `sudo ufw allow 8000`                                         |
| Service won't start         | `journalctl -u insurance-automation -n 50`                    |
| `python3` not found         | `sudo apt install python3`                                    |
| Packages fail to install    | `sudo apt install python3-dev build-essential` then retry pip |
| API errors                  | Double-check `.env` key and `AI_PROVIDER` value               |
