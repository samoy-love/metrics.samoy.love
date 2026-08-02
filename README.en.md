# metrics.samoy.love

[RU](README.md) · **EN**

Observability for the one small server that runs all of
[samoy.love](https://samoy.love): five sites, a handful of services and a
desktop game launcher. Prometheus collects, Grafana shows, one command brings
it all up.

[![CI](https://github.com/tr0llex/metrics.samoy.love/actions/workflows/ci.yml/badge.svg)](https://github.com/tr0llex/metrics.samoy.love/actions/workflows/ci.yml)
![Prometheus](https://img.shields.io/badge/Prometheus-v3.13.2-e6522c)
![Grafana](https://img.shields.io/badge/Grafana-13.1.1-f46800)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

<!--
  Dashboard screenshots go here. Drop them into docs/ and uncomment:
  ![Product dashboard](docs/dashboard-product.png)
  ![Overview dashboard](docs/dashboard-overview.png)
-->

## What makes this interesting

- **Traffic analytics with no trackers at all.** No counters, no pixels, no
  cookies: the numbers come from a separate nginx log that contains neither IP
  addresses nor User-Agents. A process that has never seen personal data
  cannot leak it by accident.
- **Product metrics, not just "the service is up".** Diff versus full
  download, bytes actually saved, hash mismatches, abandoned installs — half
  of the rules answer "people use this and it works", not "the process is
  running".
- **Metrics from processes that have nothing to listen on.** The status-page
  agent is a oneshot on a timer — between runs it simply does not exist; the
  Telegram bot listens on nothing by design. Both write to the textfile
  collector.
- **Cardinality kept on a leash.** Path and host arrive from the internet.
  Without a limit, any scanner walking `/wp-admin` and `/.env` would create a
  thousand time series overnight — and those thousand would stay in the TSDB
  forever.

## How it fits together

```mermaid
flowchart LR
    subgraph host["Host"]
        direction TB
        svc["Services<br/>ChillHub, snakes"]
        nginx["nginx"]
        timer["Status agent, bot<br/>(no port)"]
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
    timer -->|".prom via textfile"| node
    nginx -->|"samoylove_metrics.log"| nglog
    node --> prom
    nglog --> prom
    bb -->|"external probes"| prom
    prom --> graf
    graf --> gate["nginx + basic auth<br/>metrics.samoy.love"]
```

Three different delivery paths, because the sources differ: some have an HTTP
endpoint, some only a log file, and some have no process at all at scrape
time. Only nginx faces the outside: every container binds to `127.0.0.1` and
not a single port is published to the internet.

| Component | Version | Role |
|---|---|---|
| Prometheus | v3.13.2 | metric storage, 90 days of history |
| Grafana | 13.1.1 | dashboards |
| node_exporter | v1.12.1 | CPU, memory, disk, network, systemd unit state |
| blackbox_exporter | v0.28.0 | site availability and response time |
| nginxlog_exporter | v1.11.0 | static-site traffic from the nginx log |

Image versions are pinned: `latest` on arm64 periodically lags behind the
other architectures, and an update nobody asked for would break monitoring at
exactly the moment it is needed.

## What is collected

- **Host** — CPU by mode, memory, free space per filesystem, network, load
  average, uptime.
- **Services** — state of the `snakes`, `chillhub-api`, `chillhub-admin`,
  `status-agent`, `nginx`, `docker` units and the backup units.
  `status-agent` and `*-backup` run on timers, so most of the time they are
  not `active` — that is normal, not an outage.
- **Sites** — samoy.love, metro, launcher, snakes, status: status code,
  response time, certificate expiry.
- **Site traffic** — all five hosts: samoy.love, metro, snakes, status and
  launcher. Requests by host and path,
  status codes, response time, bytes served. The source is a separate nginx
  log, `samoylove_metrics.log` (its format is declared in
  [deploy-kit](https://github.com/tr0llex/deploy-kit)), which contains no IP
  and no User-Agent: client-side counters are not an option on these sites,
  the homepage README promises zero trackers.
- **ChillHub** — launcher product metrics: installs and updates by game and
  result, update mode (`diff`/`full`), bytes actually downloaded versus full
  build size, integrity checks and hash mismatches, feedback, maintenance
  windows, version activations, response codes and timings.
- **Snakes** — game metrics: humans and bots counted separately, joins, match
  duration and room size at start, causes of death, cells captured and kills,
  power-ups picked up, contracts and mutators. Separately, the health of the
  real-time part: game tick duration, WebSocket closes by reason, dropped
  messages, rejected handshakes.
- **Status page and bot** — check results, response time, certificate
  headroom, incidents, freshness of the agent's data and notifications sent.
- **The monitoring itself** — Prometheus and Grafana metrics.

There are two dashboards: **overview** (host, services, site availability) and
**product** (traffic, launcher, snakes, metro, status-page health).
One rule governs both: a panel answers a question somebody actually asks.
"Pretty, but pointless" is a reason to delete a panel, not to add one.

What is deliberately absent:

- **Client-side analytics.** Traffic is counted from server logs; the visitor
  runs nothing for it. `samoylove_metrics.log` is rotated by the standard
  `/var/log/nginx/*.log` rule (14 days) and the exporter reopens the file on
  its own.
- **Alertmanager.** The 19 rules in `prometheus/rules/` exist, notification
  routing does not: there is nobody to notify, the owner looks at the
  dashboard. The rules are an explicit list of what counts as an outage —
  their state is visible at `/prometheus/alerts`.
- **Metric backups.** Deliberately: this is operational data, there is nowhere
  and no reason to restore it from.

## Alert rules

Names and summaries are in English; the comments explaining *why* each
threshold looks the way it does are in Russian, next to the rules.

| Group | Alert | Fires when |
|---|---|---|
| `infra-host` | `DiskSpaceLow` | less than 10% free on `/` for 15 min |
| `infra-host` | `MemoryLow` | less than 10% memory available for 15 min |
| `infra-host` | `CpuSaturated` | CPU above 90% for 30 min |
| `infra-services` | `ServiceDown` | an always-on systemd unit is not `active` |
| `infra-sites` | `SiteDown` | external probe fails for 5 min |
| `infra-sites` | `CertificateExpiringSoon` | certificate expires within 14 days |
| `infra-sites` | `ProbeLatencyHigh` | probe (DNS + TLS + response) over 3 s |
| `product-sites` | `SiteErrorRateHigh` | over 5% of responses are 5xx |
| `product-sites` | `TrafficCollapse` | traffic 5× below the same hours a week ago |
| `product-sites` | `SiteLatencyHigh` | server-side p95 above 1 s |
| `product-sites` | `AccessLogParseFailing` | `log_format` and the exporter format diverged |
| `product-chillhub` | `InstallFailureRateHigh` | over 40% of installs did not finish |
| `product-chillhub` | `DiffUpdatesNotWorking` | over 80% of updates downloaded the full build |
| `product-chillhub` | `HashMismatchesHigh` | over 10 files failed the manifest in an hour |
| `product-chillhub` | `LauncherErrorRateHigh` | over 5% of launcher responses are 5xx |
| `product-chillhub` | `TelemetryRejected` | over 20 rejected telemetry calls in an hour |
| `product-status` | `StatusAgentStale` | the agent has not polled for 10 min |
| `product-status` | `StatusBotStale` | the bot has not updated its metrics for 10 min |
| `product-status` | `TelegramDeliveryFailing` | the bot cannot deliver messages |

Every ratio rule has a small-numbers guard. An error ratio over a sample of
three requests swings between 0 and 33% on its own, and an alert that fires on
a single robot visit gets muted within a week.

## Traps this project walked into

Three of them, an evening each.

**AppArmor and systemd.** Unit state is read over the system dbus, and the
default AppArmor profile forbids a container from talking to it ("An AppArmor
policy prevents this sender…") — the systemd collector then silently returns
zero metrics. Hence `apparmor=unconfined` on node_exporter; the container
still mounts the host read-only and publishes no ports. On top of that, the
image has no `/var/run` → `/run` symlink while the exporter looks for the bus
at `/var/run/dbus/...`, so the socket is mounted at that exact path.

**`IPAddressDeny=any` on somebody else's unit.** `snakes.service` runs with
`IPAddressAllow=localhost` and `IPAddressDeny=any`, so the kernel drops any
connection that is not from `127.0.0.1`. Prometheus lives in its own network
namespace and arrives from the docker bridge address, so instead of metrics it
got a timeout — silently, since the kernel drops the packets and the service
log stays empty. Working around another service's deliberate restriction from
the monitoring stack was not an option: the whole point of it is that the game
process is reachable only by nginx on the same machine. It was untangled on
the snakes side, in two parts: a unit drop-in opens exactly the docker bridge
subnets, and the game brings up a separate listener on the bridge address that
serves `/metrics` and nothing else — otherwise `/ws` and the static files
would have opened to containers along with it, bypassing nginx. The target has
been `up` since. For the same reason the ChillHub services expose
metrics on `172.17.0.1` rather than `127.0.0.1`: that address is reachable
from the host and its containers and does not answer on the server's public
address.

**Mounting a single file means mounting an inode.** `prometheus.yml` is bind
mounted as a file. Put a new file on top of it (`tar x`, `mv`, `rsync` without
`--inplace`) and the inode changes: the container keeps reading the old file
and cheerfully reports that the config was reloaded — with unchanged targets.
Edit the file in place, or recreate the container. Directories are unaffected:
new rule and dashboard files are picked up as they are.

## Installation

The nginx config does not live here but in
[deploy-kit](https://github.com/tr0llex/deploy-kit), which stays the single
source of truth for nginx.

The directory on the server is `/opt/samoylove-metrics`.

```bash
# 1. Code to the server
rsync -a --exclude .git --exclude .env ./ ubuntu@SERVER:/opt/samoylove-metrics/

# 2. Secrets (once; a second run overwrites nothing)
ssh ubuntu@SERVER 'bash /opt/samoylove-metrics/server/bootstrap.sh'

# 3. Start
ssh ubuntu@SERVER 'cd /opt/samoylove-metrics && sudo docker compose up -d'

# 4. nginx — through deploy-kit only, never by hand in /etc/nginx
sudo /opt/deploy-kit/server/nginx-apply.sh \
    --app samoylove-metrics \
    --conf /opt/deploy-kit/nginx/sites/metrics.samoy.love.conf \
    --dest /etc/nginx/sites-available/metrics.samoy.love.conf --enable
```

Step 4 requires an existing `metrics.samoy.love` A record and a certificate:

```bash
sudo certbot certonly --webroot -w /var/www/metrics-acme -d metrics.samoy.love
```

Without the certificate `nginx -t` fails on the missing file and
`nginx-apply.sh` rolls the config back.

Startup after a reboot is handled by `restart: unless-stopped` plus an enabled
`docker.service` — `restart` alone is not enough if the daemon itself does not
come up with the system.

## Operating it

Dashboards: **https://metrics.samoy.love/** (Grafana), Prometheus at
**https://metrics.samoy.love/prometheus/**.

Two gates: nginx basic auth first, then the Grafana login. Passwords live on
the server only and are not in this repository: basic auth in
`/etc/nginx/.htpasswd-metrics`, the Grafana admin in
`/opt/samoylove-metrics/.env`.

**Adding a target.** Edit `prometheus/prometheus.yml`, then reload without a
restart (Prometheus runs with `--web.enable-lifecycle`):

```bash
rsync ... && ssh ... 'curl -s -X POST http://127.0.0.1:9090/prometheus/-/reload'
```

Keeping the inode trap above in mind — either edit in place, or:

```bash
sudo docker compose up -d --force-recreate prometheus
```

An ordinary service on the host:

```yaml
  - job_name: name
    static_configs:
      - targets: ["host.docker.internal:PORT"]
```

The service has to accept connections from the docker bridge address: if its
unit carries `IPAddressAllow=localhost`, the target stays `down` forever.

A process with nothing to listen on wants the textfile collector: write `.prom`
files into `/var/lib/node_exporter/textfile/`, write them atomically (via
`.tmp` and a rename) and always emit a timestamp — otherwise a stopped process
looks alive forever, holding its last values.

One more site to probe is a URL appended to `static_configs` of the
`blackbox-http` job; the relabeling is already in place.

**Data.** Named Docker volumes rather than directories in the repository:
history has to survive a full recreation of the containers, image version
change included.

| Volume | Contents |
|---|---|
| `samoylove-metrics_prometheus-data` | TSDB, 90 days or 8 GB |
| `samoylove-metrics_grafana-data` | Grafana database |

Dashboards and the datasource are not stored in that database: they are
provisioned from `grafana/` on every start, so losing the Grafana volume does
not lose the panels.

**Resources.** The server is small, and monitoring must not become the cause
of the outage it is supposed to warn about. Limits live in
`docker-compose.yml`: Prometheus 1 CPU / 1 GB, Grafana 1 CPU / 768 MB,
exporters 0.5 CPU / 256 MB each. Container logs are rotated (3 files, 10 MB).

## Where this sits

Everything below runs in production on a single server and ships through one
pipeline.

| Repository | Relation |
|---|---|
| [samoy.love](https://github.com/tr0llex/samoy.love) | the homepage; its traffic is counted here |
| [status.samoy.love](https://github.com/tr0llex/status.samoy.love) | the status page faces visitors; metrics face the owner. The agent writes here via textfile |
| [chillhub](https://github.com/tr0llex/chillhub) | the game launcher, source of every product metric |
| [deploy-kit](https://github.com/tr0llex/deploy-kit) | the shared release pipeline; also the nginx config and the log format |
| [metro-map](https://github.com/tr0llex/metro-map) | offline metro map; its traffic is counted here |
| [snakes](https://github.com/tr0llex/snakes) | the game; it exposes metrics through a separate listener on the bridge address (see the traps) |

## License

[MIT](LICENSE).
