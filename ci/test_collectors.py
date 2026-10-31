import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).resolve().parents[1] / 'server/monitoring-containers-collect.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def test_maintenance_is_bounded_and_scoped(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            marker = path / '.maintenance-cs2-api.json'
            marker.write_text(json.dumps(dict(app='cs2-api', written=100, until=700)))
            text = '\n'.join(collector.maintenance_lines(200, path))
            self.assertIn('site="https://cs.samoy.love/",target="cs2-api"', text)
            self.assertIn('engine=""} 700', text)
            self.assertNotIn('engine="linux"} 700', text)
            self.assertNotIn('} 700', '\n'.join(collector.maintenance_lines(700, path)))
            for data in (dict(app='snakes', written=100, until=700),
                         dict(app='cs2-api', written=100, until=701),
                         dict(app='cs2-api', written=1000, until=1100),
                         dict(app='cs2-api', written='100', until=700)):
                marker.write_text(json.dumps(data))
                text = '\n'.join(collector.maintenance_lines(200, path))
                self.assertIn('deploy_maintenance_collect_ok 0', text)
                self.assertNotIn('} 700', text)
            marker.write_text('{')
            self.assertIn('deploy_maintenance_collect_ok 0', collector.maintenance_lines(200, path))

    def test_cache_is_not_confused_with_working_set(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            for name, value in {'memory.current': '900', 'memory.max': '1000',
                                'memory.stat': 'inactive_file 500\nanon 300\n',
                                'memory.events': 'oom 4\noom_kill 2\n'}.items():
                (path/name).write_text(value)
            result = collector.memory_values(path)
            self.assertEqual(result['memory_working_set_bytes'], 400)
            self.assertEqual(result['oom_kills_total'], 2)
            (path/'memory.max').write_text('max')
            self.assertNotIn('memory_limit_bytes', collector.memory_values(path))

    def test_broken_docker_does_not_claim_containers_are_stopped(self):
        with patch.object(collector.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker', 5)):
            text = '\n'.join(collector.collect(1234))
        self.assertIn('monitoring_containers_collect_ok 0', text)
        self.assertNotIn('monitoring_container_running{', text)
        self.assertNotIn('memory_usage_bytes{', text)

    def test_missing_expected_containers_remain_visible(self):
        with patch.object(collector.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            text = '\n'.join(collector.collect(1234))
        self.assertEqual(text.count('monitoring_container_running{'), 6)
        self.assertIn('monitoring_container_running{container="samoylove-grafana"} 0', text)
        self.assertIn('monitoring_containers_collect_ok 1', text)

    def test_failed_cgroup_read_is_unknown_not_zero_usage(self):
        responses = [subprocess.CompletedProcess([], 0, 'samoylove-grafana\n', ''),
                     subprocess.CompletedProcess([], 0, '{"name":"/samoylove-grafana","running":true,"pid":123,"restarts":0,"started":"2026-10-11T00:00:00Z"}\n', '')]
        with patch.object(collector.subprocess, 'run', side_effect=responses), patch.object(collector, 'cgroup_path', side_effect=FileNotFoundError):
            text = '\n'.join(collector.collect(1234))
        self.assertIn('monitoring_container_collect_ok{container="samoylove-grafana"} 0', text)
        self.assertNotIn('memory_usage_bytes{', text)

    def test_expected_containers_match_compose(self):
        import yaml
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'docker-compose.yml').read_text(encoding='utf-8'))
        self.assertEqual(set(collector.NAMES), {s['container_name'] for s in config['services'].values()})
