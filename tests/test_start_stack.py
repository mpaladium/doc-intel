"""scripts/start_stack.sh -- the corroborator orchestrator.

The value under test is that the script is honest about what is running. The
engines degrade silently by design (an engine that can't answer contributes "no
candidate"), which is right at runtime but means a crashed sidecar or a typo'd
URL is indistinguishable from a healthy setup. In particular
`mineru.available()` only checks the env var is SET -- reachability is proven
per-call -- so exporting a URL for a dead sidecar reads as "configured" while
contributing nothing. These tests pin the behaviours that prevent that.

Exercised in `--check` mode: it probes and reports without starting anything, so
it is safe and fast in CI (no model loads, no background processes).
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "start_stack.sh"


def run_check(**env_overrides) -> subprocess.CompletedProcess:
    """Run the script in --check mode with a clean corroborator environment."""
    import os
    env = dict(os.environ)
    for key in ("INGESTION_MINERU_URL", "INGESTION_SURYA_URL",
                "INGESTION_GLM_OCR_URL", "INGESTION_GLM_OCR",
                "INGESTION_GLM_OCR_MODEL"):
        env.pop(key, None)
    env["INGESTION_GLM_OCR_AUTO"] = "0"      # no ambient Ollama probe by default
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run([str(SCRIPT), "--check"], capture_output=True, text=True,
                          env=env, cwd=SCRIPT.parents[1], timeout=180)


@pytest.fixture()
def health_server():
    """A stub answering the sidecar health payload, for adoption tests."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "engine": "stub"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", port
    finally:
        server.shutdown()


def test_check_on_a_bare_machine_succeeds_and_reports_everything():
    """The fresh-clone case: nothing configured, nothing installed. Must exit 0
    (a stack with no corroborators is a supported single-parser configuration)
    and still account for every engine."""
    r = run_check()
    assert r.returncode == 0, r.stderr
    for engine in ("glm_ocr", "mineru", "surya"):
        assert engine in r.stdout, f"{engine} missing from status table:\n{r.stdout}"
    assert "IN-PROCESS" in r.stdout          # no Ollama -> in-process, not "unavailable"
    assert "nothing started" in r.stdout


def test_check_does_not_load_the_in_process_model():
    """A status check must stay cheap: `glm_ocr.available()` would pull weights
    onto the GPU in a throwaway process, and the server would then load them
    again. The in-process backend is reported, never probed."""
    r = run_check()
    assert "IN-PROCESS" in r.stdout
    assert "loads on first use" in r.stdout


def test_healthy_sidecar_on_the_port_is_adopted(health_server):
    url, port = health_server
    r = run_check(MINERU_PORT=port)
    assert "mineru" in r.stdout and "AVAILABLE" in r.stdout
    assert "adopted" in r.stdout
    # adopted engines join the equation lane
    assert "equation lane" in r.stdout and "mineru" in r.stdout.split("equation lane")[1]


def test_dead_preconfigured_url_is_reported_and_unset():
    """The core guarantee. A URL pointing at nothing must NOT be passed through
    to the server, because `available()` would then return True and the engine
    would silently contribute nothing to consensus."""
    r = run_check(INGESTION_MINERU_URL="http://127.0.0.1:9")
    assert "UNAVAILABLE" in r.stdout
    assert "not answering" in r.stdout
    assert "URL unset" in r.stdout


def test_glm_ocr_gate_off_is_reported_as_disabled():
    r = run_check(INGESTION_GLM_OCR="0")
    assert "DISABLED" in r.stdout


def test_ollama_auto_detect_requires_the_model_to_be_pulled(health_server):
    """A reachable Ollama that lacks the model must fall back to in-process, not
    claim availability -- the health of the server says nothing about whether
    glm-ocr is actually there."""
    url, _ = health_server        # answers 200 but is not Ollama / has no model
    r = run_check(INGESTION_GLM_OCR_AUTO="1", OLLAMA_URL=url)
    assert "IN-PROCESS" in r.stdout


def test_explicit_bad_ollama_url_is_unavailable_not_silent_fallback():
    """An auto-detect miss falls back quietly (nothing was asked for), but an
    explicitly configured URL that fails must be loud -- the operator asked for
    that backend and it isn't there."""
    r = run_check(INGESTION_GLM_OCR_URL="http://127.0.0.1:9")
    assert "glm_ocr" in r.stdout and "UNAVAILABLE" in r.stdout


def test_ocr_lane_is_labelled_as_scanned_pages_only():
    """An idle Surya on a born-digital corpus is correct behaviour, not a fault;
    the table has to say so or it reads as broken."""
    r = run_check()
    assert "OCR lane" in r.stdout
    assert "scanned" in r.stdout.lower()
