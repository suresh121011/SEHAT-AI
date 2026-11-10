"""D9: rotating JWT_SECRET_KEY invalidates every issued session token, and the new secret is never printed."""

import importlib.util
import os
import stat
from pathlib import Path

import jwt
import pytest

_SPEC = importlib.util.spec_from_file_location("rotate_jwt_secret", Path(__file__).resolve().parents[2] / "scripts" / "rotate_jwt_secret.py")
rot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rot)


def _secret(path):
    return next(ln.split("=", 1)[1] for ln in path.read_text().splitlines() if ln.startswith("JWT_SECRET_KEY="))


def test_rotation_replaces_only_the_secret_keeps_permissions_and_prints_no_value(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("AI_PROVIDER=fake\nJWT_SECRET_KEY=old-secret-value-old-secret-value-1234\n# comment\nOCR_ENABLED=1\n")
    os.chmod(env, 0o600)
    assert rot.main(["--env-file", str(env)]) == 0
    new = _secret(env)
    assert new != "old-secret-value-old-secret-value-1234" and len(new) >= 48
    assert env.read_text().splitlines()[0] == "AI_PROVIDER=fake" and "OCR_ENABLED=1" in env.read_text() and "# comment" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert "rotated" in out and new not in out and "old-secret" not in out


def test_a_token_signed_before_rotation_is_rejected_after_it(tmp_path):
    env = tmp_path / ".env"
    env.write_text("JWT_SECRET_KEY=before-rotation-secret-before-rotation-secret\n")
    old = jwt.encode({"sub": "anm_demo"}, _secret(env), algorithm="HS256")
    rot.rotate(env)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(old, _secret(env), algorithms=["HS256"])


def test_a_missing_key_is_added(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AI_PROVIDER=fake")
    assert rot.rotate(env) is False and _secret(env) and env.read_text().startswith("AI_PROVIDER=fake\n")


def test_duplicate_definitions_are_all_rotated_so_no_stale_secret_stays_active(tmp_path):
    from dotenv import dotenv_values

    env = tmp_path / ".env"
    env.write_text("JWT_SECRET_KEY=first-old-first-old-first-old-first-old\nX=1\nexport JWT_SECRET_KEY=second-old-second-old-second-old\n")
    rot.rotate(env)
    active = dotenv_values(env)["JWT_SECRET_KEY"]
    assert "old" not in env.read_text() and len(active) >= 48


def test_a_secret_exported_in_the_environment_is_flagged_because_it_overrides_the_file(tmp_path, capsys, monkeypatch):
    """S-3: python-dotenv never overrides an exported variable, so rotating the file alone would change nothing."""
    env = tmp_path / ".env"
    env.write_text("JWT_SECRET_KEY=old-secret-value-old-secret-value-1234\n")
    monkeypatch.setenv("JWT_SECRET_KEY", "exported-sentinel-value-0004-exported-sentinel")
    assert rot.main(["--env-file", str(env)]) == 1
    out = capsys.readouterr().out
    assert "exported" in out.lower() and "exported-sentinel-value-0004" not in out
