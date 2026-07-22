# Corroborator engine setup (GLM-OCR, MinerU/UniMERNet, Surya)

The pipeline runs single-parser out of the box. These engines add **independent
voters** to the N-version consensus: a second/third opinion per equation, and a
second opinion per scanned page. Every one is optional — if it isn't configured
or isn't reachable, the engine reports "no candidate", you get one log line, and
ingestion continues. Nothing here is a hard dependency.

Why out-of-process: UniMERNet hard-pins `transformers==4.42.4`, Surya wants
`>=4.51`, and docling resolves `5.8.x`. One virtualenv for all three is
`uv lock`-unsatisfiable, so each model runs in its own environment behind HTTP
and the in-repo adapters (`app/pipeline/engines/*.py`) are thin stdlib clients.
This also keeps the base package clean-licensed — the conditional-licence weights
never enter this venv.

| engine | lane | env var | contract |
|---|---|---|---|
| GLM-OCR | equations + scanned OCR | `INGESTION_GLM_OCR_URL` (Ollama) *or* in-process | Ollama `/api/generate` |
| MinerU / UniMERNet | equations | `INGESTION_MINERU_URL` | `POST` PNG → `{"latex": "..."}` |
| Surya | scanned OCR | `INGESTION_SURYA_URL` | `POST` PNG → `{"text": "..."}` |

`INGESTION_SIDECAR_TIMEOUT` (default `30`, seconds) bounds every call above,
including Ollama. A timeout is "no candidate", never an error.

## The one-command path

Once the environments below exist, `scripts/start_stack.sh` brings everything up
together — it starts each sidecar whose venv it finds, validates Ollama-backed
GLM-OCR (server reachable *and* model pulled), exports only the URLs that
actually answered, prints a status table, and then starts the API server:

```bash
./scripts/start_stack.sh /path/to/pdfs     # everything available + the server
./scripts/start_stack.sh --check           # probe and report, start nothing
./scripts/start_stack.sh --sidecars-only   # corroborators only (e.g. before evaluate_dir.sh)
./scripts/start_stack.sh --no-sidecars /path/to/pdfs
```

```
[stack] corroborator status:
[stack]   glm_ocr  AVAILABLE    ollama http://127.0.0.1:11434 (model glm-ocr, auto-detected)
[stack]   mineru   AVAILABLE    sidecar http://127.0.0.1:8101 (pid 41234, log ./data/sidecar-logs/…)
[stack]   surya    UNAVAILABLE  venv /home/x/.venvs/surya not found -- see deploy/sidecars/README.md
[stack] equation lane: glm_ocr, mineru
[stack] OCR lane:      glm_ocr  (scanned/uncertain pages only -- idle on a born-digital corpus)
```

Two behaviours worth knowing, both deliberate:

- **A URL that doesn't answer is unset, not passed through.** `mineru.available()`
  only checks the variable is *set* (reachability is proven per-call), so
  forwarding a dead URL would make the engine read as configured while silently
  contributing nothing. The script would rather report `UNAVAILABLE`.
- **A failed Ollama auto-detect falls back to in-process quietly; an explicitly
  configured `INGESTION_GLM_OCR_URL` that fails is reported loudly.** Nothing was
  asked for in the first case; in the second, you asked for that backend.

Overrides: `MINERU_PORT`/`SURYA_PORT` (8101/8102), `MINERU_VENV`/`SURYA_VENV`
(the paths below), `OLLAMA_URL`, `INGESTION_GLM_OCR_AUTO=0` to skip the Ollama
probe, `SIDECAR_START_TIMEOUT` (30s readiness wait). Sidecars the script started
are stopped when it exits; ones it adopted are left alone.

> **Where the engines are actually used.** The equation lane runs on documents
> that contain equation nodes; the OCR lane runs **only on `SCANNED`/`UNCERTAIN`
> pages** (born-digital pages are skipped deliberately — "skip work
> aggressively"). On a clean born-digital corpus, Surya will legitimately never
> be called. See "Verifying an engine is actually consumed" below.

---

## 1. GLM-OCR via Ollama (recommended on a GPU box)

GLM-OCR is the default-on corroborator. It can run **in-process** via
`transformers` (no setup — it just pulls the weights on first use), or against
an **Ollama** server, which is usually what you want if the box already runs
Ollama or you'd rather keep model weights out of this venv.

```bash
# on the GPU box
ollama serve                    # if not already running as a service
ollama pull glm-ocr             # https://ollama.com/library/glm-ocr

# point the pipeline at it
export INGESTION_GLM_OCR_URL=http://127.0.0.1:11434
export INGESTION_GLM_OCR_MODEL=glm-ocr      # optional, this is the default
./scripts/start_ingestion.sh /path/to/pdfs
```

The URL may be the server root or the full `/api/generate` endpoint — both work.
Setting `INGESTION_GLM_OCR_URL` is the *only* switch: with it set the in-process
model is never loaded. `INGESTION_GLM_OCR=0` still disables the engine entirely,
whichever backend is configured.

Calls pin `temperature: 0` and a fixed `seed` so the same crop yields the same
string across runs — consensus has to be reproducible.

Startup log tells you which backend won:

```
INFO engines.glm_ocr: GLM-OCR via ollama at http://127.0.0.1:11434 (model glm-ocr)
INFO engines.glm_ocr: GLM-OCR loaded in-process on cuda
WARNING engines.glm_ocr: ollama at ... has no model 'glm-ocr' (pulled: llama3:8b) -- engine unavailable
```

Remote Ollama: `INGESTION_GLM_OCR_URL=http://gpu-box:11434`. Ollama binds
localhost by default — set `OLLAMA_HOST=0.0.0.0` on that machine, and don't
expose it to an untrusted network.

---

## 2. MinerU / UniMERNet equation sidecar

A third independent LaTeX candidate per equation (encoder-decoder, not a VLM —
genuinely different failure modes from GLM-OCR, which is the point).

```bash
# separate venv -- transformers pin conflicts with this repo's
uv venv ~/.venvs/unimernet --python 3.11
source ~/.venvs/unimernet/bin/activate
uv pip install "transformers==4.42.4" torch pillow unimernet

python deploy/sidecars/mineru_server.py --port 8101      # loads on first request
```

```bash
# back in the ingestion venv
export INGESTION_MINERU_URL=http://127.0.0.1:8101
```

Weights: `wanderkid/unimernet_base` (Apache-2.0), pulled on first load.

## 3. Surya OCR sidecar

An independent scanned-page text candidate beside RapidOCR.

```bash
uv venv ~/.venvs/surya --python 3.11
source ~/.venvs/surya/bin/activate
uv pip install "surya-ocr>=0.14" torch pillow

python deploy/sidecars/surya_server.py --port 8102
```

```bash
export INGESTION_SURYA_URL=http://127.0.0.1:8102
```

This **sidecar is a corroborator** — an independent second opinion on scanned
pages, alongside whatever engine Docling itself runs. It does not change which
engine Docling uses as the primary transcription.

**To make Surya *Docling's* OCR engine (primary, not corroborator):** Linux only;
install `docling-surya` manually on Linux (unsupported on macOS due to transformer
version conflicts), then:

```bash
export INGESTION_OCR_ENGINE=surya
./scripts/start_stack.sh
```

The engine log will confirm `docling_ocr=surya` if the plugin is available, or
`docling_ocr=rapidocr` with a warning if it cannot be imported. **Important:** when
Docling's OCR engine is Surya, the Surya *sidecar* is automatically disabled
(assemble._ocr_text_engines) — the same model cannot be both the primary
transcription and its own independent second opinion.

**Licence note:** Surya's *code* is Apache-2.0 but its *weights* are Rail-M
(conditional commercial terms). Keeping it in a sidecar means those weights
never enter this repo's environment — check the terms before production use.

> Both server scripts are reference implementations. The model-loading block is
> the version-sensitive part: `surya-ocr` and `unimernet` have both changed their
> Python APIs across releases. If your pinned version differs, that block is the
> only thing you should need to adjust — the HTTP contract above is what the
> pipeline depends on, and it is stable.

---

## Verifying an engine is actually consumed

Configuring an engine is not the same as it contributing. Check the data, not
the env:

```bash
uv run python - <<'EOF'
import collections
from pathlib import Path
from app.store.artifact_store import ArtifactStore, compute_key
from app.version import PIPELINE_VERSION

store = ArtifactStore("./data/artifacts")
pdf = Path("data/eval-samples/YOUR.pdf")
ed = store.get_edition(compute_key(pdf.read_bytes(), PIPELINE_VERSION))

def walk(n, out):
    out.append(n)
    for c in n.children: walk(c, out)
    return out

keys = collections.Counter()
for n in walk(ed.root, []):
    for k in (n.parsers or {}): keys[k] += 1
print("page classes:", ed.pipeline_provenance.get("page_classes"))
print("parser candidates:", dict(keys))
EOF
```

`glm_ocr` / `mineru` appearing means the equation lane really voted;
`surya` / `rapidocr` mean the OCR lane did. A count of `0` with the URL set is
usually correct rather than broken — the document had no equations, or no
scanned pages. Confirm with the `page classes` line.

A quick end-to-end reachability check before parsing anything:

```bash
uv run python -c "
from app.pipeline.engines import glm_ocr, mineru, surya
for e in (glm_ocr, mineru, surya):
    print(f'{e.ENGINE_NAME:8s} available={e.available()}')
"
```
