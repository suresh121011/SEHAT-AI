"""Rotate JWT_SECRET_KEY in a .env file (remediation decision D9). The new secret is never printed.

Every session token is signed with this secret (app/auth.py), and there is no per-token revocation list, so rotating it
invalidates every issued token at once: everyone signs in again. Run it on any machine that ran a frontend build with
the removed /api/session/token route (contribution 21a9761), because that route could hand the session token to page
scripts. Then restart the backend.

    cd sehat-ai/backend && ../.venv/bin/python scripts/rotate_jwt_secret.py            # ../.env (the repo's .env)
    ../.venv/bin/python scripts/rotate_jwt_secret.py --env-file /path/to/.env

The file is rewritten atomically with its permissions kept; other lines are left exactly as they were.
"""

import argparse
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

_LINE = re.compile(r"^\s*(export\s+)?JWT_SECRET_KEY\s*=.*$", re.M)


def rotate(env_file: Path) -> bool:
    """Replace (or add) JWT_SECRET_KEY with a fresh secret. Returns True if an existing value was replaced."""
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    line = f"JWT_SECRET_KEY={secrets.token_urlsafe(48)}"
    replaced = bool(_LINE.search(text))
    if replaced:
        # every definition: python-dotenv keeps the LAST one, so a stale duplicate would otherwise stay in force
        text = _LINE.sub(lambda m: (m.group(1) or "") + line, text)
    else:
        text = text + ("" if not text or text.endswith("\n") else "\n") + line + "\n"
    mode = env_file.stat().st_mode & 0o777 if env_file.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=env_file.parent, prefix=".env.rotate.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, env_file)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return replaced


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[2] / ".env")
    args = ap.parse_args(argv)
    replaced = rotate(args.env_file)
    print(f"JWT_SECRET_KEY {'rotated' if replaced else 'added'} in {args.env_file} (value not shown). "
          "Restart the backend: every existing session is now invalid and users sign in again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
