# Warm Springs Cellars — Wine Club

Static landing page for the Warm Springs Cellars allocation list, served by
nginx from a container image built in CI.

```
index.html      the whole site — markup, styles and scripts in one file
images/         winery photography and bottle shots (see images/README.md)
labels/         the official label artwork, as supplied (.ai)
fonts/          Caveat, self-hosted for the handwritten card (OFL)
tools/          regenerate the bottle shots, and sync the tiers into the page
api/            the club: applications, offers, payments, invoices, shipments
nginx.conf      serves the site and proxies the club service
Dockerfile      nginx:1.27-alpine + the site, running unprivileged
docker-compose.yml   two services behind one published port, 8086
.env.example    every secret the club service needs
.github/workflows/docker.yml   tests, then builds both images
```

## Two services, one port

```
Nginx Proxy Manager
        │  :8086
        ▼
      nginx ──────── /                    the static site
        │
        └─ proxy ──▶ api:8000             /api/   the request form and webhooks
                                          /club/  the member's offer pages
                                          /admin  the cellar desk
```

Only nginx is published. The club service is reachable from nothing but nginx,
on the compose network, which is why it does not need TLS of its own.

## The images

`.github/workflows/docker.yml` runs the club's test suite first, then builds
`linux/amd64` and `linux/arm64` for both
`ghcr.io/pel-matiasvaldivia/warmspringscellars` and
`…-api`, on every push to
`main` (tagged `latest`), on branch pushes (tagged with the branch name), and
on `v*` tags (tagged with the semver). Pull requests build without pushing, so
a broken Dockerfile fails before it lands. Auth uses the built-in
`GITHUB_TOKEN` — there is no secret to configure.

## Deploying on the VPS

```bash
git clone https://github.com/pel-matiasvaldivia/warmspringscellars.git
cd warmspringscellars
cp .env.example .env
#   SECRET_KEY:     openssl rand -base64 48   — generate once, never change it
#   ADMIN_PASSWORD: anything long
$EDITOR .env
docker compose up -d
```

`docker compose` refuses to start until `SECRET_KEY` and `ADMIN_PASSWORD` are
set, deliberately: a club that signs invitation links with a key it invented at
boot invalidates every link already in a member's inbox each time it restarts.

Everything else in `.env` is optional. Without Stripe keys, checkout is
simulated and no money moves; without SMTP, every email is written to the data
volume as a `.eml` file and the invitation link is shown on the desk. That is a
working installation you can walk a colleague through — it just cannot take
money yet. `GET /api/health` lists whatever is still a stub.

Only **8086** is published, mapped to nginx on 8080 inside the container. Put
Nginx Proxy Manager in front of it:

| NPM field       | Value                                    |
| --------------- | ---------------------------------------- |
| Scheme          | `http`                                   |
| Forward host    | the VPS IP, or `warmspringscellars` if you attach it to NPM's docker network |
| Forward port    | `8086` (or `8080` when sharing a network) |
| Websockets      | off                                      |
| Block exploits  | on                                       |

Request the certificate in NPM and enable *Force SSL* there — TLS terminates at
the proxy, and the container speaks plain HTTP behind it.

If the package is private, log in once before the first pull:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <your-github-user> --password-stdin
```

### If NPM runs on this same host

Publishing `8086` on all interfaces means the site is directly reachable on
that port, bypassing the proxy. Either bind it to loopback in
`docker-compose.yml`:

```yaml
ports:
  - "127.0.0.1:8086:8080"
```

…or drop `ports` entirely, attach both containers to a shared network, and
point NPM at `warmspringscellars:8080`.

## Running the club

The whole commercial flow, from the form to the doorstep, is described in
[`api/FLOW.md`](api/FLOW.md) — including the two things it deliberately does not
do yet (US shipping compliance, and sales tax).

Day to day, whoever works the desk wants the operations manual instead: what to
open, what to press, and what to check when something does not add up. It is
the same document in two languages — [`OPERATIONS.md`](OPERATIONS.md) and
[`OPERACIONES.md`](OPERACIONES.md).

The desk is at `/admin`, behind the password in `.env`. Restrict it by address
in `nginx.conf` too; the commented `allow`/`deny` lines are there for it.

| What | Where |
| --- | --- |
| Read and approve requests | `/admin` |
| Members, and pausing or cancelling them | `/admin/memberships` |
| Orders and their invoices | `/admin/orders` |
| The pick list, and tracking numbers | `/admin/shipments` |
| Raise a release's orders | `/admin/releases` |
| QuickBooks, and what the books are still owed | `/admin/accounting` |

### The offer

```bash
cd api && pip install -r requirements-dev.txt && python -m pytest tests -q
```

The club's prices and benefits live in **one** file, `api/app/tiers.json`. The
offer page renders from it, the invoice bills from it, and
`tools/sync_tiers.py` writes it into the landing page:

```bash
python3 tools/sync_tiers.py           # update index.html from the catalogue
python3 tools/sync_tiers.py --check   # CI uses this; fails if they disagree
```

Edit the JSON, run the tool, commit both. A visitor shown $180 who is then
charged $205 is a bug that costs trust rather than pixels.

### Backups

Everything the club knows is in the `club-data` volume: the SQLite database —
which also holds the QuickBooks queue and the Intuit tokens — the invoice PDFs,
and the outbox. Back it up.

```bash
docker run --rm -v warmspringscellars_club-data:/var -v "$PWD":/backup alpine \
  tar czf /backup/club-$(date +%F).tar.gz -C /var .
```

### Pinning a version

`docker-compose.yml` follows `latest` by default. To pin:

```bash
TAG=v1.0.0 docker compose up -d
```

## Updating

```bash
docker compose pull && docker compose up -d
```

Nothing is baked in at runtime, so a redeploy is a pull plus a restart. The
image carries the page and the photos, so editing `index.html` means a new
build — push to the branch and let CI produce the image.

## Notes

- The container runs as the unprivileged `nginx` user with a read-only root
  filesystem, all capabilities dropped, and tmpfs for nginx's scratch dirs.
- `/healthz` backs the container healthcheck. Don't expose it through NPM.
- `set_real_ip_from` covers the RFC1918 ranges so access logs show the real
  client IP from `X-Forwarded-For` rather than the proxy's address.
- `index.html` is served with `Cache-Control: no-cache` so edits appear
  immediately; `images/` is cached for a week.
