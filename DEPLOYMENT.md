# Hosting this for free

Three routes that cost nothing, with the trade-offs stated plainly. Skip to
[Option A](#option-a--oracle-cloud-always-free--cloudflare-pages-recommended)
if you just want the best one.

> **Free tiers change.** Fly.io and Railway both removed theirs recently, and
> this document was written in September 2026. Check the provider's current
> terms before committing time — the shape of the advice below holds even when
> a specific name drops off the list.

---

## What you're actually deploying

Knowing these three numbers makes every hosting decision obvious:

| | Size | Consequence |
|---|---|---|
| Python dependencies | **464 MB** (scipy 113, pandas 76, sklearn 51, numpy 45) | **Serverless is out.** Vercel/Netlify functions cap at 250 MB, as does AWS Lambda. You need a container host or a VM. |
| Trained model artifacts | **980 KB** | Small enough to commit to the repo or bake into an image. Not a storage problem. |
| Database (1,930 matches) | **1.1 MB** | Tiny — but it holds user accounts, so it must survive restarts. This is the only thing that genuinely needs persistence. |

So: **static frontend anywhere** (easy, free forever), **containerised backend**
(the constraint), and **a database that persists** (the thing free tiers are
stingy about).

---

## Option A — Oracle Cloud Always Free + Cloudflare Pages (recommended)

The only option here that is always-on, has a real disk, and never expires.

**You get:** 4 ARM cores, 24 GB RAM, 200 GB storage, permanently free, no trial
clock. That's enough to run the API, the database, model training, *and* a local
LLM for the assistant's optional rewriter.

**Costs you:** a credit card for identity verification (not charged), and you
administer the VM yourself. ARM capacity is sometimes unavailable in busy
regions — if you hit "out of capacity", try a different availability domain or
retry over a few days.

### 1. Backend on the VM

Create an **Ampere A1 (ARM)** instance running Ubuntu, and open port 8000 in
both the OCI security list *and* the instance firewall (Oracle images ship with
iptables rules that silently drop traffic otherwise — this is the single most
common thing people get stuck on).

```bash
sudo apt update && sudo apt install -y python3-venv git nginx
git clone https://github.com/Princegates/Sports-Prediction-System.git
cd Sports-Prediction-System/backend

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Generate a real signing key — the default is published in this repo.
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
echo "CORS_ALLOW_ORIGINS=https://your-site.pages.dev" >> .env

# Empty -> serving, in one command (~30s for one league)
SUPERADMIN_EMAIL=you@example.com SUPERADMIN_PASSWORD='pick-something-strong' \
  python scripts/bootstrap.py
```

Run it under systemd so it survives reboots:

```ini
# /etc/systemd/system/predictions.service
[Unit]
Description=AI Football Prediction API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Sports-Prediction-System/backend
EnvironmentFile=/home/ubuntu/Sports-Prediction-System/backend/.env
ExecStart=/home/ubuntu/Sports-Prediction-System/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now predictions
```

Put nginx in front with a free Let's Encrypt certificate (`sudo certbot --nginx`)
so the API is served over HTTPS — browsers block a plain-HTTP API called from an
HTTPS site, so this isn't optional.

**Keep SQLite.** At this scale it's faster than a network database and needs no
second service. Back it up with a cron job: `sqlite3 sports_prediction.db ".backup /home/ubuntu/backup.db"`.

### 2. Frontend on Cloudflare Pages

Connect the repo at [dash.cloudflare.com](https://dash.cloudflare.com) → Workers
& Pages → Create → Pages, then:

| Setting | Value |
|---|---|
| Build command | `npm run build` |
| Build output directory | `dist` |
| Root directory | `frontend` |
| Environment variable | `VITE_API_URL` = `https://api.yourdomain.com` |

`VITE_API_URL` is **baked in at build time**, not read at runtime — if you change
it you must trigger a rebuild, or the site will keep calling the old address.

`frontend/public/_redirects` is already in the repo and handles SPA routing.
Without it every deep link (`/how-it-works`, a shared match URL) 404s on refresh.

Cloudflare Pages has unlimited bandwidth on the free plan and never sleeps.

---

## Option B — Render + Neon + Cloudflare Pages (no credit card)

Easiest to stand up, and free without any card. The catch is real: **the backend
sleeps after ~15 minutes of inactivity**, so the first request after a quiet
period takes 50+ seconds while the container wakes. For a members-only tool
that's often tolerable; for a public landing page it means visitors may hit a
long blank load.

Render's free tier also has **no persistent disk**, which is why this option
needs an external database.

### 1. Database — Neon (free Postgres)

Create a project at [neon.tech](https://neon.tech) and copy the connection
string. It will start with `postgres://`, which SQLAlchemy 2 rejects — the app
[normalizes that automatically](backend/app/config.py), so paste it as-is.

### 2. Backend — Render web service

The repo ships a `render.yaml` blueprint, so the fastest route is **Blueprints →
New Blueprint Instance** and picking this repo — Render then configures the
service and generates `SECRET_KEY` itself. To do it by hand instead:
New → Web Service → connect the repo:

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Runtime | Docker |
| Instance type | Free |

Environment variables:

```
DATABASE_URL      = <your Neon connection string>
SECRET_KEY        = <python -c "import secrets; print(secrets.token_urlsafe(48))">
CORS_ALLOW_ORIGINS= https://your-site.pages.dev
```

`PORT` is injected by Render and the Dockerfile already binds to it.

### 3. Seed it once

Because the disk is ephemeral but the database isn't, run the bootstrap against
the Neon URL **from your own machine** — once. The data then lives in Postgres
and survives every restart:

```bash
cd backend
DATABASE_URL='postgres://...your neon url...' \
SUPERADMIN_EMAIL=you@example.com SUPERADMIN_PASSWORD='pick-something-strong' \
  python scripts/bootstrap.py
```

### 4. Model artifacts

The trained `.joblib` files live on the container's ephemeral disk and vanish on
every restart. When they're missing the API still works, but the gradient-boosting
model silently drops out of the ensemble and predictions get worse without any
visible error. Two fixes, both fine:

- **Commit them** (980 KB total) — delete `backend/model_artifacts/` and `*.joblib`
  from `.gitignore`, run the bootstrap locally, commit the artifacts. Simplest.
- **Retrain on boot** — add `python scripts/bootstrap.py --skip-data` as a Render
  pre-deploy command. Adds ~20s per deploy and needs no committed binaries.

### 5. Frontend

Same as Option A — Cloudflare Pages, pointing `VITE_API_URL` at your
`*.onrender.com` URL.

---

## Option C — Hugging Face Spaces (no card, generous RAM)

Worth knowing about because it's free, needs no credit card, gives you ~16 GB
RAM, and supports Docker directly. Storage is ephemeral, so pair it with Neon
exactly as in Option B. Create a Space with the Docker SDK, point it at the repo,
and set the same environment variables as secrets. Spaces sleep after inactivity
on the free tier too.

---

## Keeping predictions fresh, free

Predictions don't regenerate themselves. Rather than paying for a scheduler, use
GitHub Actions — 2,000 free minutes a month, which is far more than this needs:

```yaml
# .github/workflows/refresh-predictions.yml
name: Refresh predictions
on:
  schedule:
    - cron: "0 6 * * *"   # 06:00 UTC daily
  workflow_dispatch:       # and a manual button

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r backend/requirements.txt
      - run: python scripts/bootstrap.py --days-ahead 10
        working-directory: backend
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

Add `DATABASE_URL` under the repo's Settings → Secrets → Actions. On Option A
(a VM with its own disk) use a plain `cron` entry on the box instead.

A scheduled job also keeps a sleeping free-tier backend warm, which partly
offsets Option B's cold starts.

---

## Emailing access codes (optional)

Access codes work without any of this: generate one, copy it, send it to the
buyer however you already talk to them. Configure SMTP only if you would
rather the system send it for you.

Set these on the **backend** service (Render → Environment):

| Variable | Example | Notes |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | Required to enable sending |
| `SMTP_PORT` | `587` | 587 = STARTTLS, 465 = implicit TLS. Picked automatically from this number. |
| `SMTP_USER` | `you@gmail.com` | Omit only if your relay does not authenticate |
| `SMTP_PASSWORD` | an **app password**, not your login | See below |
| `SMTP_FROM` | `Match Intelligence <you@gmail.com>` | Required. What the recipient sees. |
| `PUBLIC_SITE_URL` | `https://your-site.pages.dev` | Put in the email so they know where to redeem |

**Gmail needs an app password, not your account password.** Turn on 2-Step
Verification, then create one under Google Account → Security → App passwords.
Gmail's free sending limit is around 500 messages a day, which is far more
than issuing access codes will ever need. Any other provider works the same
way — only the host and port change.

Two deliberate behaviours worth knowing:

- **A failed send never loses the code.** Delivery is attempted after the code
  is committed, and a failure is reported back rather than raised. You get the
  code plus a note saying it did not send, instead of an error and no code.
- **With nothing configured, the admin page says so** rather than silently not
  sending. Ticking "Email it to them" on an unconfigured server returns the
  code with an explanation attached.

## Before you go live

1. **Set `SECRET_KEY`.** Session tokens are HMAC-signed with it and the default
   is published in this repository — anyone who has read it can mint a token for
   any account, including a Super Admin. The API logs a warning at startup while
   the default is in place.
2. **Set `CORS_ALLOW_ORIGINS`** to your real frontend origin, not `*`.
3. **Serve the API over HTTPS.** An HTTPS page cannot call an HTTP API; the
   browser blocks it and the site will look broken with only a console error.
4. **Create the first Super Admin** — it can't come through the approval flow,
   for obvious reasons. `python scripts/create_superadmin.py --email you@example.com`.
5. **Import more than one season.** Accuracy measured on this codebase: 40.3%
   with two seasons of training data, 45.9% with five. More history is the
   cheapest accuracy you will ever buy. Pass `--leagues` and `--seasons` to the
   bootstrap.
6. **Check the model artifacts exist** on the running host. Their absence
   degrades predictions silently.

---

## What this costs

Nothing, on every path above. To be specific about where the "free" has edges:

| | Option A (Oracle) | Option B (Render + Neon) |
|---|---|---|
| Money | $0 | $0 |
| Credit card | Required for ID check, not charged | Not required |
| Always on | Yes | No — sleeps after ~15 min idle |
| Cold start | None | 50s+ |
| Disk | 200 GB, persistent | Ephemeral (DB is external) |
| Setup effort | Higher (you admin a VM) | Lower (connect a repo) |
| Expires | Never | Neon pauses idle projects; Render free tier terms change |

If you want it to *feel* like a real product, Option A is worth the extra hour.
If you want it live this afternoon, Option B gets you there.

---

## Launch checklist (Option B, fastest path to live)

Roughly 20 minutes, no credit card. Do them in this order — step 3 needs the
URL from step 2.

### 1. Database — Neon (3 min)

1. Sign up at [neon.tech](https://neon.tech) with GitHub.
2. Create a project. On the creation screen:
   - **Postgres database: on.** Leave *Object storage*, *Functions*,
     *AI gateway* and **Neon Auth** off. Neon Auth in particular would
     duplicate this app's own auth and tempt a half-migration that breaks the
     Super Admin approval gate.
   - **Region: this is the one choice you cannot undo.** Neon fixes a
     project's region at creation, and it must match the region you deploy the
     backend to — the API issues several queries per request, so a
     cross-continent hop between them lands in every page load. `render.yaml`
     is set to `frankfurt`; if you pick something else here, change it there
     too. (Both platforms offer Frankfurt, Ohio, Virginia, Oregon and
     Singapore on their free tiers.)
3. Copy the connection string from the dashboard. It looks like
   `postgres://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`.
   Keep it somewhere for steps 2 and 4.

#### Using Supabase instead

Supabase's free Postgres works the same way, and nothing in the app is
Neon-specific — but it offers *three* connection strings and the choice
matters more than the dashboard suggests.

**Take the pooler string, not the direct one.** Supabase's direct connection
(`db.<ref>.supabase.co`) resolves to IPv6 only unless you pay for the IPv4
add-on, and free hosts commonly have no IPv6 egress — which surfaces as a
connection timeout that looks like a firewall problem. The pooler hosts
(`aws-0-<region>.pooler.supabase.com`) are reachable over IPv4. Check this
against Supabase's current docs before spending time on it; it is the kind
of detail that changes.

**Either pooler port works, and the app adapts to both:**

| Port | Mode | Notes |
|---|---|---|
| 5432 | Session | Behaves like an ordinary connection. Fine. |
| 6543 | Transaction | Better under many clients, but no prepared statements. |

Transaction mode hands each transaction whichever backend is free, so a
statement prepared on one is missing on the next. psycopg prepares
automatically once a query has run five times, so this fails *late* and
looks random: the app works, then the queries it runs most often start
raising `prepared statement "_pg3_0" already exists` while rarer ones keep
working. `app/db/session.py` detects port 6543 (and pgbouncer's 6432) and
disables automatic preparation, so you don't have to think about it — but
that is why the port is worth noticing if you ever see that error.

**The egress budget is the same problem, not a smaller one.** Supabase's
free tier allows 5 GB of transfer a month, as Neon's does. The 153x egress
reduction in this repo is what makes either fit; without it, five leagues
retrained nightly spends the month's allowance in two nights on either
provider.

**Region still matters**, for the same reason as above: match it to the
Render region in `render.yaml`.

### 2. Backend — Render (5 min)

1. Sign up at [render.com](https://render.com) with GitHub.
2. Go to **Blueprints → New Blueprint Instance**, pick this repo and the
   branch. Render reads `render.yaml` and configures the service itself.
3. It will ask for two values:
   - `DATABASE_URL` → the Neon string from step 1
   - `CORS_ALLOW_ORIGINS` → leave `https://REPLACE-ME.pages.dev` for now; you
     correct it in step 5 once you know the real one
4. Deploy. First build takes ~5 minutes (464 MB of dependencies).
5. Note the service URL: `https://sports-prediction-api-xxxx.onrender.com`.
   Check `<that-url>/api/health` returns `{"status":"ok"}`.

`SECRET_KEY` is generated by Render automatically — nothing to do.

### 3. Seed the database (2 min, from your own machine)

The container's disk is wiped on every restart, but Neon isn't. Run this once
locally and the data is permanent:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

DATABASE_URL='postgres://...your neon string...' \
SUPERADMIN_EMAIL='you@example.com' \
SUPERADMIN_PASSWORD='pick-something-strong' \
  python scripts/bootstrap.py
```

Imports six seasons, trains, backtests, generates predictions and creates your
Super Admin account. Takes about a minute.

### 3b. Running the seed on Windows

The commands above are Unix shell. PowerShell differs in three ways that each
produce a confusing error rather than an obvious one:

| Unix | PowerShell |
|---|---|
| `python3` | `python` (or `py -3`) |
| `cmd1 && cmd2` | Run them as separate lines — `&&` is a parse error in PowerShell 5 |
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `VAR=value python ...` | `$env:VAR = 'value'` on its own line, first |
| `\` line continuation | Put the whole command on one line |

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DATABASE_URL = 'postgresql://...your neon string...'
$env:SUPERADMIN_EMAIL = 'you@example.com'
$env:SUPERADMIN_PASSWORD = 'pick-something-strong'

python scripts/bootstrap.py --leagues "English Premier League" "Spanish La Liga" "Italian Serie A" "German Bundesliga" "French Ligue 1"
```

Three things that bite specifically on Windows:

- **`Activate.ps1` may be blocked** by execution policy ("running scripts is
  disabled on this system"). `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
  unblocks it for that window only.
- **Use single quotes for the connection string.** In double quotes PowerShell
  expands `$`, so a password containing one is silently mangled and you get an
  authentication failure that looks like wrong credentials.
- **Environment variables are per-window.** Open a new tab and they're gone.

Confirm the database is reachable *before* the ~9-minute run, rather than
finding out at the end:

```powershell
python -c "from app.config import get_settings; from sqlalchemy import create_engine, text; s=get_settings(); e=create_engine(s.normalized_database_url); print('OK:', e.connect().execute(text('select version()')).scalar()[:60])"
```

`OK: PostgreSQL 17...` means you're connected.

### 4. Frontend — Cloudflare Pages (5 min)

1. Sign up at [dash.cloudflare.com](https://dash.cloudflare.com).
2. **Workers & Pages → Create → Pages → Connect to Git**, pick this repo.
3. Build settings:
   - Framework preset: **None**
   - Build command: `npm run build`
   - Build output directory: `dist`
   - Root directory: `frontend`
4. Under **Environment variables**, add
   `VITE_API_URL` = your Render URL from step 2 (no trailing slash).
5. Save and deploy. Note your URL: `https://something.pages.dev`.

### 5. Close the loop (2 min)

1. Back in Render → your service → Environment, set `CORS_ALLOW_ORIGINS` to
   the real `https://something.pages.dev`. Save; it redeploys.
2. Open your Pages URL. The welcome page should show real match counts and a
   real accuracy figure — if the numbers are there, the frontend is talking to
   the backend correctly.
3. Sign in with the Super Admin account from step 3 and check `/app/admin`.

### 6. Keep it fresh

Add `DATABASE_URL` under the repo's **Settings → Secrets and variables →
Actions**. The `refresh-predictions.yml` workflow then regenerates predictions
daily on GitHub's free minutes, and incidentally keeps the sleeping backend
warm.

### If something doesn't work

| Symptom | Cause |
|---|---|
| Welcome page loads but all stats are zero | Step 3 didn't run, or ran against a different database |
| Stats missing entirely, console shows CORS errors | `CORS_ALLOW_ORIGINS` doesn't exactly match your Pages origin |
| Everything 404s on refresh but works when clicking | `_redirects` missing from the build — check Root directory is `frontend` |
| First load takes ~60s | Expected on Render free; the service was asleep |
| Frontend calls `localhost:8000` | `VITE_API_URL` wasn't set at build time — set it and **redeploy**, it's baked in |
