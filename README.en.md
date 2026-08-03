# metrics.samoy.love

[Русский](README.md) · English

[![CI](https://github.com/tr0llex/metrics.samoy.love/actions/workflows/ci.yml/badge.svg)](https://github.com/tr0llex/metrics.samoy.love/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Prometheus](https://img.shields.io/badge/Prometheus-v3.13.2-e6522c)
![Grafana](https://img.shields.io/badge/Grafana-13.1.1-f46800)

Monitoring and product analytics for the single small server that runs all of
[samoy.love](https://samoy.love): five sites, several services and a desktop
launcher — brought up with one command.

## Why

"The service is alive" and "people are using it, and it works for them" are
different questions, and only the first one is answered by a process being up.
A green healthcheck says nothing about updates that fail halfway, hashes that
do not match, or a page nobody has opened in a week. So the stack collects both
layers at once: host and unit state on one dashboard, product numbers on the
other.

The second constraint is privacy. Traffic is counted from a separate nginx log
that physically has no IP, no User-Agent, no Referer and no query string — a
process that never saw personal data cannot leak it by accident.

Some things never reach the server at all: metro computes routes in the
browser and works offline, a PWA is installed straight from the cache, a
"Play" press happens before any connection. Those are counted by an empty
`POST /e/<event>` answered with 204 — no body, no parameters, no cookie, no
session or visitor id. The log line holds the host, the event name and zero
bytes, so one person's path cannot be reconstructed: there is nothing to join
the lines on. What happened is counted; who did it is not.

The homepage used to promise "zero trackers". Once these counters appeared
that stopped being literally true, so the promise now names what is actually
absent instead.

![Overview dashboard](docs/dashboard-overview.png)

![Product dashboard](docs/dashboard-product.png)

<sub>Frames captured from the live stand with
[`docs/render-dashboards.sh`](docs/render-dashboards.sh). Empty panels in the
ChillHub section are not a fault: the deployed launcher binary is older than
its exporter, and those metrics appear with the next release.</sub>

## How it works

```mermaid
flowchart LR
    subgraph host["Server"]
        direction TB
        svc["Services<br/>ChillHub, snakes"]
        nginx["nginx"]
        timer["Status agent, bot<br/>(no listening port)"]
    end

    subgraph compose["Docker Compose (127.0.0.1 only)"]
        direction TB
        node["node_exporter"]
        nglog["nginxlog_exporter"]
        bb["blackbox_exporter"]
        prom["Prometheus<br/>90 days"]
        graf["Grafana"]
    end

    svc -->|"pull /internal/metrics"| prom
    timer -->|".prom into textfile"| node
    nginx -->|"traffic and event<br/>logs"| nglog
    node --> prom
    nglog --> prom
    bb -->|"probes from outside"| prom
    prom --> graf
    graf --> gate["nginx + basic auth<br/>metrics.samoy.love"]
```

**Three delivery paths, because the sources differ.** Some components expose an
HTTP endpoint and are simply scraped. nginx has no endpoint at all, so traffic
comes from a log file through an exporter. The status agent is a oneshot unit
driven by a timer and the Telegram bot deliberately listens nowhere — between
runs there is no process to scrape, so both write `.prom` files into the
textfile collector. Such a file must carry its own timestamp, otherwise a
stopped process keeps looking alive forever with its last values.

**Nothing is published outwards.** Every container binds to `127.0.0.1`, and
the only door is nginx with basic auth in front of Grafana and of Prometheus at
`/prometheus/`. Scrape targets on the host are reached over the docker bridge
address rather than loopback: a container lives in its own network namespace
and cannot see the host's `127.0.0.1`. That is also why ChillHub services
expose `/internal/metrics` on their own port instead of behind the public API —
product numbers must not become reachable through a forgotten `location`.

**Two host-level obstacles were worked around deliberately.** Unit state is
read over the system dbus, and the default AppArmor profile forbids a container
from talking to it — the systemd collector then returns zero metrics without a
word, hence `apparmor=unconfined` on node_exporter, which still mounts the host
read-only and exposes no port. Separately, `snakes.service` runs with
`IPAddressAllow=localhost` and `IPAddressDeny=any`, so the kernel silently
dropped every scrape from the bridge. Rather than defeating another service's
intentional restriction from the monitoring side, it was opened on the snakes
side and in two parts: a drop-in admits exactly the docker bridge subnets, and
the game exposes a separate listener that serves only `/metrics` — otherwise
`/ws` and the static files would have been opened to containers past nginx.

**Cardinality is capped at the exporter.** Path and host arrive from the
internet. Without a limit, any scanner walking `/wp-admin` and `/.env` would
create a thousand time series overnight, and those series would stay in the
TSDB forever.

**Image versions are pinned.** `latest` on arm64 periodically drifts ahead of
the other architectures, and an upgrade nobody asked for would break monitoring
exactly when it is needed. The CI reads the Prometheus version out of
`docker-compose.yml` rather than repeating it: two places holding a version
drift apart, one does not.

**Everything is provisioned from files.** Dashboards, the data source and alert
rules live in the repository and are applied at every start, so losing the
Grafana volume does not lose the panels. Prometheus history lives in a named
Docker volume instead, because it must survive a full recreate of the
containers, including an image upgrade.

Collected: host resources and systemd unit state; availability, response time
and certificate expiry for all five sites; traffic per host and path from the
nginx log; ChillHub product metrics (installs and updates by game and outcome,
diff versus full download, bytes actually transferred against the full build
size, integrity checks and hash mismatches, feedback, maintenance, version
activations); Snakes gameplay and real-time health (humans and bots counted
separately, match length, causes of death, tick duration, WebSocket drops by
reason); status page and bot results; and the monitoring stack itself. Two
dashboards, one rule for both: a panel answers a question that actually gets
asked. Deliberately absent are client-side analytics, an Alertmanager and
backups of the metrics themselves — those are operational data with nowhere and
no reason to restore them from.

## Stack

| Component | Version | Role |
|---|---|---|
| Prometheus | v3.13.2 | metric storage, 90 days or 8 GB |
| Grafana | 13.1.1 | dashboards |
| node_exporter | v1.12.1 | CPU, memory, disk, network, systemd unit state, textfile |
| blackbox_exporter | v0.28.0 | availability, response time and certificate expiry |
| nginxlog_exporter | v1.11.0 | site traffic and interface events from nginx logs |

Docker Compose, scrape interval 30 s. Resource limits are set in
`docker-compose.yml` (Prometheus 1 CPU / 1 GB, Grafana 1 CPU / 768 MB,
exporters 0.5 CPU / 256 MB each): the server is small, and monitoring must not
become the cause of the outage it is supposed to report.

## Quick start

The nginx configuration lives in
[deploy-kit](https://github.com/tr0llex/deploy-kit), which stays the single
source of truth for nginx. The directory on the server is
`/opt/samoylove-metrics`.

```bash
# 1. Code onto the server
rsync -a --exclude .git --exclude .env ./ ubuntu@SERVER:/opt/samoylove-metrics/

# 2. Secrets (once; a repeated run overwrites nothing)
ssh ubuntu@SERVER 'bash /opt/samoylove-metrics/server/bootstrap.sh'

# 3. Start
ssh ubuntu@SERVER 'cd /opt/samoylove-metrics && sudo docker compose up -d'

# 4. nginx — only through deploy-kit, never by hand in /etc/nginx
sudo /opt/deploy-kit/server/nginx-apply.sh \
    --app samoylove-metrics \
    --conf /opt/deploy-kit/nginx/sites/metrics.samoy.love.conf \
    --dest /etc/nginx/sites-available/metrics.samoy.love.conf --enable
```

Step 4 needs the `metrics.samoy.love` A record and a certificate to exist
already:

```bash
sudo certbot certonly --webroot -w /var/www/metrics-acme -d metrics.samoy.love
```

Without the certificate `nginx -t` fails on the missing file and
`nginx-apply.sh` honestly reverts the config.

Step 4 is only needed for the very first install. After that the config ships
itself: the target sets `NGINX_CONF` and `NGINX_DEST` (see
`.deploy-kit/prod.env`), and every deploy applies it through the same
`nginx-apply.sh` — with a diff in the log, a backup, and a revert if
`nginx -t` fails.

Access goes through two gates: nginx basic auth first, then the Grafana login.
Credentials exist only on the server — basic auth in
`/etc/nginx/.htpasswd-metrics`, the Grafana admin in
`/opt/samoylove-metrics/.env` — and are never kept in this repository.

## Structure

| Path | Purpose |
|---|---|
| `docker-compose.yml` | the whole stack: images, limits, volumes, `127.0.0.1` bindings |
| `prometheus/prometheus.yml` | scrape targets and intervals |
| `prometheus/rules/infra.yml` | alert rules: host, units, certificates, availability |
| `prometheus/rules/product.yml` | alert rules: launcher, snakes, traffic, status page |
| `grafana/dashboards/overview.json` | host, services, site availability |
| `grafana/dashboards/product.json` | traffic, launcher, snakes, metro, status page |
| `grafana/provisioning/` | data source and dashboard provisioning |
| `blackbox/blackbox.yml` | probe modules for blackbox_exporter |
| `nginxlog/nginxlog.yml` | log format and cardinality limits for the traffic exporter |
| `server/bootstrap.sh` | one-time secret setup on the server |
| `docs/render-dashboards.sh` | capture dashboard PNGs, run on the server |
| `.env.example` | template for `.env` (Grafana admin) |

## What is guaranteed, and what checks it

Configs here break silently: a typo in a rule or a drifted data source uid
brings nothing down — the panel simply shows nothing and the alert never fires.
So CI checks exactly that class of failure.

| Guarantee | Enforced by |
|---|---|
| Prometheus config parses and rules are valid | `promtool check config` and `check rules` in CI |
| The promtool version matches the deployed one | CI reads the version out of `docker-compose.yml` |
| Dashboards are valid JSON and point at the real data source | uid comparison against `grafana/provisioning/datasources` in CI |
| The compose file resolves | `docker compose config --quiet` in CI |
| YAML across the repository is well formed | `yamllint` in CI |
| Personal data cannot leak into metrics | the log format has no IP, User-Agent, Referer or query string |
| A scanner cannot flood the TSDB | cardinality limits in `nginxlog/nginxlog.yml` |
| Metrics are not reachable from the internet | containers bind `127.0.0.1`, nginx with basic auth in front |
| History survives a container recreate | named Docker volumes, not directories in the repository |
| Panels survive the loss of the Grafana volume | dashboards and data source are provisioned from files |

19 alert rules exist without an Alertmanager: there is nobody to page, and the
owner watches the dashboard. They are kept as an explicit list of what counts
as an incident, and their state is visible at `/prometheus/alerts`.

## Adding a scrape target

Edit `prometheus/prometheus.yml` and reload without a restart (Prometheus runs
with `--web.enable-lifecycle`):

```bash
rsync ... && ssh ... 'curl -s -X POST http://127.0.0.1:9090/prometheus/-/reload'
```

A plain service on the host:

```yaml
  - job_name: name
    static_configs:
      - targets: ["host.docker.internal:PORT"]
```

The service has to accept connections from the docker bridge address: if its
unit carries `IPAddressAllow=localhost`, the target stays `down` forever — and
silently, because the kernel drops the packets and the service log says
nothing.

A process with nothing to listen on takes the textfile collector instead: drop
a `.prom` file into `/var/lib/node_exporter/textfile/`, write it atomically
(`.tmp` plus rename) and always include a timestamp.

One more site to probe for availability only needs its URL appended to the
`blackbox-http` job — relabeling is already in place.

**Mind the inode.** `prometheus.yml` is mounted as a file. Replacing it
(`tar x`, `mv`, `rsync` without `--inplace`) changes the inode, the container
keeps reading the old file and cheerfully reports the config as reloaded — with
unchanged targets. Edit in place, or recreate the container:

```bash
sudo docker compose up -d --force-recreate prometheus
```

Directories are unaffected: new rule and dashboard files are picked up as is.

## Part of samoy.love

One domain, one server, one pipeline, one status page, one monitoring stack.

| Project | What it is |
|---|---|
| [samoy.love](https://github.com/tr0llex/samoy.love) | Homepage and project showcase: Astro, WebGL background, no cookies or third parties |
| [chillhub](https://github.com/tr0llex/chillhub) | ChillHub — Windows game launcher: diff updates, hash control, Go admin panel |
| [snakes](https://github.com/tr0llex/snakes) | Browser territory-capture multiplayer: Go, WebSocket, binary protocol |
| [metro-map](https://github.com/tr0llex/metro-map) | Offline PWA with the Moscow metro map: routing on the client, Canvas 2D |
| [status.samoy.love](https://github.com/tr0llex/status.samoy.love) | Status page: uptime, versions, incidents; Go agent plus an external watchdog |
| [metrics.samoy.love](https://github.com/tr0llex/metrics.samoy.love) | This repository: monitoring and product analytics |
| [deploy-kit](https://github.com/tr0llex/deploy-kit) | Shared release pipeline; also the nginx config and the log format |

## Contacts

Alexey Samoylov — [alex@samoy.love](mailto:alex@samoy.love) ·
[t.me/tr0llex](https://t.me/tr0llex) ·
[github.com/tr0llex](https://github.com/tr0llex)

## License

[MIT](LICENSE).
