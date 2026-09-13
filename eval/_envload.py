"""Minimal .env loader for the eval HOST process.

The eval suite runs on your laptop (pytest / compare_to_baseline.py), launched by the Makefile —
which does NOT source .env. So we load `deploy/.env` here ourselves, mainly to pick up the judge's
Vertex AI config (GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION) without
making you `set -a; source .env` first.

.env is AUTHORITATIVE here (we OVERRIDE existing env vars). Why: a stale `GOOGLE_GENAI_USE_VERTEXAI`
left exported in your shell from an earlier `source .env` would otherwise silently override the file
and quietly send the judge back to the rate-limited AI-Studio tier. Runtime-only knobs the Makefile
passes on the command line (LLM_MAX_RPM, JUDGE_DATASET) are deliberately NOT kept in .env, so they
stay overridable. No dependency on python-dotenv.
"""
import os

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def load_dotenv(path: str = _DEFAULT_PATH) -> None:
    """Load KEY=VALUE lines from `path` into os.environ (.env wins over the inherited environment)."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            # Strip surrounding quotes; inline comments are intentionally NOT parsed — keep it dumb,
            # .env here holds plain values.
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ[key] = val
