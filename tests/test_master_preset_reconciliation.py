import json
import os
import pathlib
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "opentofu/scripts/reconcile-k8s-master-preset.sh"


class MasterPresetReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.current = 1673
        self.target_zone = "msk-1"
        self.status = "started"
        self.patches = []
        self.patch_status = 200
        self.converges = True
        self.node_converges = True
        self.presets = {
            1673: dict(id=1673, cpu=2, ram=2048, disk=30, master_nodes_count=1,
                       type="master", availability_zone="msk-1"),
            1675: dict(id=1675, cpu=4, ram=8192, disk=60, master_nodes_count=1,
                       type="master", availability_zone="msk-1"),
        }
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, body, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                if self.path == "/api/v1/presets/k8s":
                    case.presets[1675]["availability_zone"] = case.target_zone
                    self.respond({"k8s_presets": list(case.presets.values())})
                elif self.path == "/api/v1/k8s/clusters/1091532":
                    self.respond({"cluster": dict(preset_id=case.current,
                                                  availability_zone="msk-1", status=case.status)})
                elif self.path == "/api/v1/k8s/clusters/1091532/master-nodes":
                    preset = case.presets[case.current if case.node_converges else 1673]
                    self.respond({"nodes": [dict(preset, preset_id=preset["id"], status="active")]})
                else:
                    self.respond({}, 404)

            def do_PATCH(self):
                if self.path != "/api/v1/k8s/clusters/1091532/master-nodes":
                    self.respond({}, 404)
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.patches.append(body)
                if case.converges and case.patch_status == 200:
                    case.current = body["preset_id"]
                self.respond({}, case.patch_status)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_reconcile(self, preset=1675):
        return subprocess.run(
            ["bash", str(SCRIPT), "1091532", str(preset)],
            env={**os.environ, "TIMEWEB_API": f"http://127.0.0.1:{self.server.server_port}",
                 "TIMEWEB_TOKEN": "test-token", "MASTER_PRESET_TIMEOUT_SECONDS": "0"},
            capture_output=True, text=True, timeout=10,
        )

    def test_resizes_through_dedicated_endpoint_and_verifies_node(self):
        result = self.run_reconcile()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.patches, [{"preset_id": 1675}])
        self.assertIn("4 CPU / 8192 MiB RAM / 60 GiB disk", result.stdout)

    def test_already_matching_master_is_not_restarted(self):
        self.current = 1675
        result = self.run_reconcile()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.patches, [])

    def test_downgrade_is_rejected_without_mutation(self):
        self.current = 1675
        result = self.run_reconcile(1673)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patches, [])

    def test_different_zone_is_rejected_without_mutation(self):
        self.target_zone = "spb-3"
        result = self.run_reconcile()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patches, [])

    def test_busy_cluster_is_not_mutated(self):
        self.status = "updating"
        result = self.run_reconcile()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patches, [])

    def test_failed_patch_is_not_retried_or_reported_as_success(self):
        self.patch_status = 500
        result = self.run_reconcile()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.patches), 1)

    def test_api_success_without_resize_fails_verification(self):
        self.converges = False
        result = self.run_reconcile()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timed out", result.stderr)

    def test_matching_cluster_preset_with_old_node_resources_fails(self):
        self.node_converges = False
        result = self.run_reconcile()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timed out", result.stderr)


if __name__ == "__main__":
    unittest.main()
