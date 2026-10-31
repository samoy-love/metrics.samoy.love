#!/usr/bin/env python3
"""Read the monitoring stack's Docker state and cgroup v2 limits into textfile.

No daemon, socket mount in an exporter, Docker stats streaming or new port.
Run as root inside the Linux machine hosting the monitoring Compose project.
Missing/stopped containers remain visible; failed reads never become zero usage.
"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

NAMES = tuple('samoylove-' + n for n in (
    'prometheus', 'grafana', 'renderer', 'node-exporter',
    'blackbox', 'nginxlog'))
OUT = Path(os.environ.get('MONITORING_CONTAINERS_OUT',
                         '/var/lib/node_exporter/textfile/monitoring_containers.prom'))
CGROUP = Path('/sys/fs/cgroup')
MAINTENANCE = Path('/var/lib/deploy-kit/events')
# Only availability checks use these labels. Resource and data-loss alarms
# continue to work during deployment. Unknown apps cannot mute known targets.
DEPLOY_TARGETS = {
    'cs2-api': ('https://cs.samoy.love/', 'cs2-api', 'cs2-api.service', ''),
    'cs2-chain': ('', '', 'cs2-chain.service', ''),
    'cs2-worker': ('', '', 'cs2-worker.target', ''),
    'cs2-gc': ('', '', 'cs2-gc.service', ''),
    'cs2-render': ('', '', 'cs2-render.service', 'linux'),
    'cs2-render-win': ('', '', '', 'hlae'),
    'snakes': ('https://snakes.samoy.love/', 'snakes', 'snakes.service', ''),
    'chillhub-api': ('https://launcher.samoy.love/', 'chillhub-api', 'chillhub-api.service', ''),
    'chillhub-admin': ('', 'chillhub-admin', 'chillhub-admin.service', ''),
    'metrics': ('', '', 'samoylove-metrics.service', ''),
    'samoylove': ('https://samoy.love/', '', '', ''),
    'metro': ('https://metro.samoy.love/', '', '', ''),
    'die': ('https://die.samoy.love/', '', '', ''),
    'status-site': ('https://status.samoy.love/', '', '', ''),
    'chillhub-site': ('https://launcher.samoy.love/', '', '', ''),
}
STATUS_CHECKS = {
    'cs2-api': ('cs2-health',), 'snakes': ('snakes', 'snakes-health'),
    'metrics': ('metrics-health',), 'samoylove': ('samoylove',),
    'metro': ('metro', 'metro-sw'), 'die': ('die', 'die-sw', 'die-version'),
    'status-site': ('status-page', 'status-miniapp'),
    'chillhub-site': ('launcher',), 'chillhub-admin': ('launcher-admin',),
    'chillhub-api': ('launcher-build',),
}


def maintenance_lines(now, directory=MAINTENANCE):
    lines = ['# TYPE deploy_maintenance_until_seconds gauge',
             '# TYPE deploy_maintenance_check_until_seconds gauge',
             '# TYPE deploy_maintenance_collect_ok gauge']
    ok = 1
    for app, (site, target, unit, engine) in DEPLOY_TARGETS.items():
        until = 0
        try:
            data = json.loads((directory / ('.maintenance-' + app + '.json')).read_text())
            written, expiry = data['written'], data['until']
            if (data['app'] != app or type(written) is not int or type(expiry) is not int
                    or not 0 <= expiry - written <= 600 or written > now + 30):
                raise ValueError('invalid maintenance window')
            until = expiry if expiry > now else 0
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            ok = 0
        tags = dict(app=app, site=site, target=target, unit=unit, engine=engine)
        labels = ','.join(k + '=' + label(v) for k, v in tags.items())
        lines.append(f'deploy_maintenance_until_seconds{{{labels}}} {until}')
        for check in STATUS_CHECKS.get(app, ()):
            lines.append(f'deploy_maintenance_check_until_seconds{{app={label(app)},check={label(check)}}} {until}')
    lines.append(f'deploy_maintenance_collect_ok {ok}')
    return lines


def label(value):
    return json.dumps(str(value), ensure_ascii=True)


def memory_values(path):
    current = int((path / 'memory.current').read_text())
    maximum = (path / 'memory.max').read_text().strip()
    stat = dict(line.split() for line in (path / 'memory.stat').read_text().splitlines())
    events = dict(line.split() for line in (path / 'memory.events').read_text().splitlines())
    out = {'memory_usage_bytes': current,
           'memory_working_set_bytes': max(0, current - int(stat.get('inactive_file', 0))),
           'oom_kills_total': int(events['oom_kill'])}
    if maximum != 'max':
        out['memory_limit_bytes'] = int(maximum)
    return out


def cgroup_path(pid):
    relative = next(x[3:] for x in Path(f'/proc/{pid}/cgroup').read_text().splitlines()
                    if x.startswith('0::'))
    path = (CGROUP / relative.lstrip('/')).resolve()
    if not path.is_relative_to(CGROUP):
        raise ValueError('cgroup outside delegated root')
    return path


def collect(now):
    lines = [
        '# HELP monitoring_container_running Expected monitoring container is running.',
        '# TYPE monitoring_container_running gauge',
        '# HELP monitoring_container_memory_usage_bytes Total charged cgroup memory.',
        '# TYPE monitoring_container_memory_usage_bytes gauge',
        '# HELP monitoring_container_memory_working_set_bytes Charged memory minus inactive file cache.',
        '# TYPE monitoring_container_memory_working_set_bytes gauge',
        '# HELP monitoring_container_memory_limit_bytes Finite cgroup memory limit.',
        '# TYPE monitoring_container_memory_limit_bytes gauge',
        '# HELP monitoring_container_oom_kills_total OOM kills in the current container cgroup.',
        '# TYPE monitoring_container_oom_kills_total counter',
        '# HELP monitoring_container_restarts_total Docker restarts of the current container.',
        '# TYPE monitoring_container_restarts_total counter',
        '# TYPE monitoring_container_start_timestamp_seconds gauge',
        '# TYPE monitoring_container_collect_ok gauge',
        '# TYPE monitoring_containers_collect_ok gauge',
        '# TYPE monitoring_containers_collect_timestamp_seconds gauge',
    ]

    def emit(metric, value, name=None):
        labels = '' if name is None else '{container=' + label(name) + '}'
        lines.append(f'monitoring_container_{metric}{labels} {value}')

    # Inspect only state fields: never read environment variables or credentials.
    fmt = ('{"name":{{json .Name}},"running":{{.State.Running}},'
           '"pid":{{.State.Pid}},"restarts":{{.RestartCount}},'
           '"started":{{json .State.StartedAt}}}')
    try:
        listing = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}',
                                  '--filter', 'label=com.docker.compose.project=samoylove-metrics'],
                                 capture_output=True, text=True, timeout=5, check=True)
        names = sorted(set(listing.stdout.splitlines()) & set(NAMES))
        records = {}
        if names:
            result = subprocess.run(['docker', 'inspect', '--format', fmt, *names],
                                    capture_output=True, text=True, timeout=5, check=True)
            records = {d['name'].lstrip('/'): d for d in map(json.loads, result.stdout.splitlines())}
        for name in NAMES:
            d = records.get(name)
            emit('running', int(bool(d and d['running'])), name)
            if d is None or not d['running']:
                emit('collect_ok', 1, name)
                continue
            try:
                values = memory_values(cgroup_path(d['pid']))
                started = datetime.datetime.fromisoformat(d['started'].replace('Z', '+00:00')).timestamp()
                values.update(restarts_total=d['restarts'], start_timestamp_seconds=started)
                for metric, value in values.items():
                    emit(metric, value, name)
                emit('collect_ok', 1, name)
            except (OSError, ValueError, KeyError, StopIteration):
                emit('collect_ok', 0, name)
        lines.append('monitoring_containers_collect_ok 1')
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        lines.append('monitoring_containers_collect_ok 0')
    lines.append(f'monitoring_containers_collect_timestamp_seconds {now:.0f}')
    return lines


def main():
    now = time.time()
    lines = collect(now) + maintenance_lines(now)
    tmp = OUT.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    os.replace(tmp, OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
