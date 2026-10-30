"""Every test runs with the fictional team in tests/pomodo.test.toml, set before config.py is first imported, and
with none of the shell's sign-in or local-copy settings, so a developer's environment can't change the results."""
import os
from pathlib import Path

os.environ["POMODO_CONFIG"] = str(Path(__file__).resolve().parent / "pomodo.test.toml")
os.environ["SECRET_KEY"] = "test-secret-key-" + "x" * 48
for name in ("DEV_USER", "DEMO", "DEV_FIXTURE", "K_SERVICE", "POMODO_PRODUCTION", "PUBLIC_URL",
             "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
    os.environ.pop(name, None)
