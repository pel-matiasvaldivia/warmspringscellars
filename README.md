# Warm Springs Cellars — Wine Club

Static landing page for the Warm Springs Cellars allocation list, served by
nginx from a container image built in CI.

```
index.html      the whole site — markup, styles and scripts in one file
images/         winery photography and bottle shots (see images/README.md)
labels/         the official label artwork, as supplied (.ai)
fonts/          Caveat, self-hosted for the handwritten card (OFL)
tools/          regenerate the bottle shots from the labels
nginx.conf      server config: listens on 8080 inside the container
Dockerfile      nginx:1.27-alpine + the site, running unprivileged
docker-compose.yml   pulls the published image, publishes host port 8086
.github/workflows/docker.yml   builds and pushes to GHCR
```

## The image

`.github/workflows/docker.yml` builds `linux/amd64` and `linux/arm64` and
pushes to `ghcr.io/pel-matiasvaldivia/warmspringscellars` on every push to
`main` (tagged `latest`), on branch pushes (tagged with the branch name), and
on `v*` tags (tagged with the semver). Pull requests build without pushing, so
a broken Dockerfile fails before it lands. Auth uses the built-in
`GITHUB_TOKEN` — there is no secret to configure.

## Deploying on the VPS

```bash
git clone https://github.com/pel-matiasvaldivia/warmspringscellars.git
cd warmspringscellars
docker compose up -d
```

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
