"""Shared rules for the SEHAT AI agent hooks (HARNESS.md). Standard library only; Python 3.8+.

Pure functions, no I/O. `bash_guard.py` and `edit_guard.py` turn the decisions into Claude Code PreToolUse hook
output; backend/tests/ci/test_agent_harness.py pins the behaviour.

What the hooks enforce (the owner's standing rules, AGENTS.md "Git and data rules"):
  * deny: broad staging (`git add -A/./-u/-f`), `git commit -a`, `git reset`, `git clean`, `git stash` (except
    list/show), force or delete pushes, commands that discard working-tree changes, history rewrites, reading or
    writing a `.env` secrets file, touching the shared `sehat.db`, `sudo`, deleting `/`, `~`, `.` or the repo, and
    piping a download into a shell;
  * ask: `git commit` and `git push` (the owner says "commit this"), global installs, and changes to the
    safety-critical files and the owner's work in progress listed below.

Limits (also in HARNESS.md): a hook sees the command text, not what a script does when it runs. `python -c`,
`git apply`, Makefiles and other programs can still change files; the hooks are a second line of defence behind
AGENTS.md, code review and CI, not a sandbox.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Iterable, List, Optional, Sequence, Tuple

DENY = "deny"
ASK = "ask"

Decision = Tuple[str, str]  # (DENY or ASK, reason)

# ── Paths ────────────────────────────────────────────────────────────────────────────────────────────────────────
# Safety-critical code and the guards around it: an agent asks the owner before changing these (AGENTS.md).
PROTECTED_DIRS: Tuple[str, ...] = (
    "backend/app/rules/",  # deterministic urgency rules, sources, engine: the only urgency authority
    "backend/app/privacy/",  # AI gateway, PII redaction, adapters
    ".claude/hooks/",  # this harness
    ".github/workflows/",  # CI gates
)
PROTECTED_FILES: Tuple[str, ...] = (
    "backend/app/ai/guard.py",
    "backend/app/ai/guardrails.py",
    "backend/app/ai/grounding.py",
    "backend/app/ai/redflag_keywords.py",
    "backend/app/ai/maker.py",
    "backend/app/consent.py",
    "backend/app/consent_notice.py",
    "backend/app/audit.py",
    "backend/app/auth.py",
    "backend/app/sessions.py",
    "backend/app/oidc.py",
    "backend/app/ratelimit.py",
    "backend/app/security_events.py",
    "backend/app/patient_identity.py",
    "backend/app/case_triage.py",
    "backend/app/review_queue.py",
    "backend/app/database.py",
    "backend/app/config.py",
    "backend/scripts/ci_skip_gate.py",
    "backend/tests/privacy/test_urgency_authority.py",
    "backend/tests/privacy/test_boundary.py",
    "backend/tests/rules/test_isolation.py",
    ".claude/settings.json",
    "AGENTS.md",
    "CLAUDE.md",
)
# The owner's unregistered work in progress (docs/implementation/h0-1-wip-routers.md). Untracked in the main
# checkout; new code must use other paths (for example app/referral_notes/), so it never collides with these.
WIP_DIRS: Tuple[str, ...] = (
    "backend/app/cds/",
    "backend/app/patient/",
    "backend/app/surveillance/",
    "backend/tests/cds/",
    "backend/tests/patient/",
    "backend/tests/surveillance/",
)
WIP_FILES: Tuple[str, ...] = (
    "backend/app/routes/referrals.py",
    "backend/app/routes/cds.py",
    "backend/app/routes/patient.py",
    "backend/app/routes/surveillance.py",
    "frontend/package.json",
)

_ENV_ALLOWED = ("example", "sample", "template", "dist")
_ENV_IN_TEXT = re.compile(r"(?:^|[^A-Za-z0-9_.-])\.env(?:\.(?!(?:example|sample|template|dist)\b)[A-Za-z0-9_-]+)*(?![A-Za-z0-9_.-])")
_DB_IN_TEXT = re.compile(r"(?:^|[^A-Za-z0-9_.-])sehat\.db(?![A-Za-z0-9_])")

REASON_ENV = ("A .env file holds secrets: agents never read, print, copy or write it (AGENTS.md). "
              "Use .env.example for variable names; the owner edits .env by hand.")
REASON_DB = ("sehat.db is the shared demo database: agents use a scratch DATABASE_PATH only (AGENTS.md). "
             "Example: DATABASE_PATH=$TMPDIR/sehat-scratch/sehat.db.")
REASON_PROTECTED = ("{path} is safety-critical (AGENTS.md 'Protected files'). Changes need the owner's approval, "
                    "tests first, and the sehat-safety-change skill.")
REASON_WIP = ("{path} is the owner's unregistered work in progress (docs/implementation/h0-1-wip-routers.md). "
              "Do not change or reactivate it without the owner's approval.")


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _variants(entry: str) -> Tuple[str, ...]:
    # Commands often run from backend/, so "app/rules/" must match as well as "backend/app/rules/".
    return (entry, entry[len("backend/"):]) if entry.startswith("backend/") else (entry,)


def _match_dir(path: str, dirs: Iterable[str]) -> Optional[str]:
    probe = "/" + _norm(path).rstrip("/") + "/"
    for d in dirs:
        for v in _variants(d):
            if "/" + v in probe:
                return d
    return None


def _match_file(path: str, files: Iterable[str]) -> Optional[str]:
    probe = "/" + _norm(path).rstrip("/")
    for f in files:
        for v in _variants(f):
            if probe.endswith("/" + v):
                return f
    return None


def is_env_file(path: str) -> bool:
    name = _norm(path).rstrip("/").rsplit("/", 1)[-1]
    if name == ".env":
        return True
    return name.startswith(".env.") and name[5:].split(".")[0] not in _ENV_ALLOWED


def is_shared_db(path: str) -> bool:
    name = _norm(path).rstrip("/").rsplit("/", 1)[-1]
    return name == "sehat.db" or name.startswith("sehat.db.") or name.startswith("sehat.db-")


def protected_reason(path: str) -> Optional[str]:
    """ASK reason when `path` is a WIP or safety-critical file, else None."""
    hit = _match_dir(path, WIP_DIRS) or _match_file(path, WIP_FILES)
    if hit:
        return REASON_WIP.format(path=hit)
    hit = _match_dir(path, PROTECTED_DIRS) or _match_file(path, PROTECTED_FILES)
    if hit:
        return REASON_PROTECTED.format(path=hit)
    return None


def _ancestor_of_protected(path: str) -> Optional[str]:
    """`rm -rf backend` deletes protected files too: the target is a parent of a protected path."""
    p = _norm(path).rstrip("/")
    if not p or p.startswith("/"):
        return None
    for entry in PROTECTED_DIRS + PROTECTED_FILES + WIP_DIRS + WIP_FILES:
        for v in _variants(entry):
            if v.startswith(p + "/"):
                return entry
    return None


def edit_decision(path: str) -> Optional[Decision]:
    """Decision for an Edit/Write/MultiEdit/NotebookEdit of `path` (absolute or repo-relative)."""
    if not path:
        return None
    if is_env_file(path):
        return DENY, REASON_ENV
    if is_shared_db(path):
        return DENY, REASON_DB
    reason = protected_reason(path)
    return (ASK, reason) if reason else None


# ── Shell commands ───────────────────────────────────────────────────────────────────────────────────────────────
_PUNCT = set("();<>|&")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_INTERPRETERS = _SHELLS | {"python", "python3", "node", "perl", "ruby"}
_PREFIXES = {"command", "builtin", "exec", "nohup", "time", "noglob", "caffeinate", "unbuffer"}
_SEARCH = {"grep", "egrep", "fgrep", "rg", "ag"}
_METADATA = {"ls", "test", "[", "[[", "stat", "file"}
_GIT_SAFE_FOR_ENV = {"check-ignore", "status", "ls-files", "log", "diff", "show", "blame"}
_MUTATING_ALL = {"rm", "mv", "tee", "truncate", "shred", "unlink", "chmod", "chown", "dd"}
_MUTATING_LAST = {"cp", "install", "rsync", "ln"}
_BROAD_RM = {"/", "/*", "~", "~/", "~/*", "$HOME", "${HOME}", "$HOME/", ".", "./", "./*", "..", "../", "*", ".*", ".git", ".git/"}


def strip_heredocs(command: str) -> str:
    """Drop heredoc bodies: their text is data for a file, not commands (a note may mention `git add -A`)."""
    return _HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], command)


def _flatten(command: str) -> str:
    """Outside quotes: newlines and backticks become `;`, comments are dropped (a comment must not hide a second
    command once the lines are joined), and a backslash-newline continues the line."""
    out: List[str] = []
    quote = None
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(" " if command[i + 1] == "\n" else c + command[i + 1])
            i += 2
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
        elif c in ("\n", "`"):
            out.append(" ; ")
        elif c == "#" and (not out or out[-1][-1:] in (" ", "\t", ";", "|", "&", "(")):
            j = command.find("\n", i)
            if j == -1:
                break
            i = j
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def tokenize(command: str) -> Optional[List[str]]:
    lexer = shlex.shlex(_flatten(strip_heredocs(command)), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes
        return None


def _is_punct(token: str) -> bool:
    return bool(token) and all(ch in _PUNCT for ch in token)


def _is_redirect(token: str) -> bool:
    return _is_punct(token) and ("<" in token or ">" in token)


def _segments(tokens: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """[(separator before the segment, tokens)]. Redirect operators stay inside their segment."""
    segs: List[Tuple[str, List[str]]] = []
    cur: List[str] = []
    sep = ""
    for t in tokens:
        if (_is_punct(t) and not _is_redirect(t)) or t in ("{", "}"):
            if cur:
                segs.append((sep, cur))
            cur, sep = [], t
        elif t != "!":
            cur.append(t)
    if cur:
        segs.append((sep, cur))
    return segs


def _split_redirects(seg: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
    """(words, output targets, other redirect operands)."""
    words: List[str] = []
    outs: List[str] = []
    others: List[str] = []
    i = 0
    while i < len(seg):
        t = seg[i]
        if _is_redirect(t):
            if i + 1 < len(seg) and not _is_punct(seg[i + 1]):
                (outs if ">" in t else others).append(seg[i + 1])
                i += 2
            else:
                i += 1
            continue
        words.append(t)
        i += 1
    return words, outs, others


def _base(word: str) -> str:
    return word.rstrip("/").rsplit("/", 1)[-1]


def _strip_prefixes(words: Sequence[str]) -> Tuple[List[str], List[str]]:
    """(command words, nested command strings from `env -S`). Skips VAR=value, env, nohup, time, xargs and similar."""
    nested: List[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        b = _base(w)
        if _ASSIGN.match(w):
            i += 1
        elif b == "env":
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGN.match(words[i])):
                if words[i] in ("-S", "--split-string") and i + 1 < len(words):
                    nested.append(words[i + 1])
                    i += 2
                elif words[i] in ("-u", "--unset", "-C", "--chdir"):
                    i += 2
                else:
                    i += 1
        elif b in _PREFIXES:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
        elif b in ("nice", "timeout"):
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-n", "-s", "-k", "--signal", "--kill-after") else 1
            if b == "timeout" and i < len(words):
                i += 1  # duration
        elif b == "xargs":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a") else 1
        else:
            break
    return list(words[i:]), nested


def _short_flags(args: Sequence[str]) -> set:
    flags: set = set()
    for a in args:
        if a == "--":
            break
        if re.fullmatch(r"-[A-Za-z]+", a):
            flags.update(a[1:])
    return flags


def _positionals(args: Sequence[str], with_value: Iterable[str] = ()) -> List[str]:
    takes = set(with_value)
    out: List[str] = []
    skip = False
    after_dashes = False
    for a in args:
        if skip:
            skip = False
            continue
        if after_dashes:
            out.append(a)
        elif a == "--":
            after_dashes = True
        elif a.startswith("-") and a != "-":
            skip = a in takes
        else:
            out.append(a)
    return out


_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}


def _git_split(words: Sequence[str]) -> Tuple[str, List[str]]:
    """("add", ["-A"]) for `git -C repo add -A`: global options are skipped."""
    i = 1
    while i < len(words):
        w = words[i]
        if w in _GIT_GLOBAL_WITH_VALUE:
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w, list(words[i + 1:])
    return "", []


def _git(words: Sequence[str]) -> List[Decision]:
    sub, args = _git_split(words)
    if not sub:
        return []
    short = _short_flags(args)
    pos = _positionals(args)
    deny = lambda reason: [(DENY, reason)]  # noqa: E731

    if sub == "add":
        broad = {"-A", "--all", "-u", "--update", "--no-ignore-removal", "-f", "--force"}
        if any(a in broad for a in args) or short & set("Auf") or any(p in (".", "./", ":/", ":/*", "*") for p in pos):
            return deny("Stage files by name (`git add <path> ...`). `git add -A`, `.`, `-u` and `-f` are not used in this repo (AGENTS.md).")
    elif sub == "commit":
        if "--all" in args or "a" in short:
            return deny("`git commit -a` stages everything. Stage files by name, then commit (AGENTS.md).")
        return [(ASK, "Commits happen only when the owner says \"commit this\" (AGENTS.md). Confirm this commit.")]
    elif sub == "reset":
        return deny("`git reset` is not used in this repo (AGENTS.md). To unstage a file: `git restore --staged <file>`.")
    elif sub == "clean":
        return deny("`git clean` deletes untracked files, including the owner's work in progress (AGENTS.md).")
    elif sub == "stash":
        if not (pos and pos[0] in ("list", "show")):
            return deny("`git stash` is shared by every worktree and session; it can take or drop someone else's work (AGENTS.md).")
    elif sub == "push":
        forced = any(a == "--force" or a.startswith("--force-with-lease") or a in ("--force-if-includes", "--mirror", "--delete", "--prune")
                     for a in args)
        if forced or short & set("fd") or any(p.startswith(("+", ":")) for p in pos[1:]):
            return deny("Force, mirror and delete pushes are not used in this repo (AGENTS.md).")
        return [(ASK, "Pushing publishes the branch to GitHub. Confirm this push.")]
    elif sub == "checkout":
        if "--force" in args or "f" in short or "--" in args or any(p in (".", "./", ":/", "*") for p in pos):
            return deny("This `git checkout` overwrites working-tree files. Use `git switch <branch>` to change branch.")
    elif sub == "restore":
        staged = "--staged" in args or "S" in short
        worktree = "--worktree" in args or "W" in short
        if not staged or worktree:
            return deny("`git restore` discards working-tree changes. Only `git restore --staged <file>` (unstage) is allowed.")
    elif sub == "switch":
        if "--force" in args or "--discard-changes" in args or "f" in short:
            return deny("`git switch --force/--discard-changes` throws away local changes.")
    elif sub == "branch":
        if "--force" in args or short & set("DMCf") or ("--delete" in args and "--force" in args):
            return deny("Force-deleting or force-moving a branch is not used in this repo. `git branch -d` is fine.")
    elif sub == "worktree":
        if pos[:1] == ["remove"] and ("--force" in args or "f" in short):
            return deny("`git worktree remove --force` deletes uncommitted work in that worktree.")
    elif sub == "rm":
        if any(p in (".", "./", ":/", "*") for p in pos):
            return deny("`git rm` of the whole tree is not allowed. Remove files by name.")
    elif sub in ("filter-branch", "filter-repo"):
        return deny("History rewrites are not used in this repo.")
    elif sub == "reflog" and pos[:1] and pos[0] in ("expire", "delete"):
        return deny("Deleting reflog entries removes the way back from a mistake.")
    elif sub == "update-ref" and ("-d" in args or "--stdin" in args):
        return deny("Deleting refs directly is not used in this repo.")
    return []


def _installs(cmd: str, args: Sequence[str]) -> List[Decision]:
    ask = [(ASK, "Global or system installs need the owner's explicit permission (AGENTS.md). Install into the "
                 "project venv (.venv) or frontend/node_modules instead.")]
    first = args[0] if args else ""
    if cmd in ("npm", "pnpm", "bun") and any(a in ("-g", "--global", "--location=global") for a in args):
        return ask
    if cmd == "yarn" and first == "global":
        return ask
    pip = cmd in ("pip", "pip3") or (cmd == "uv" and first == "pip") or (cmd.startswith("python") and "pip" in args[:3])
    if pip and "install" in args and any(a in ("--user", "--break-system-packages", "--system") for a in args):
        return ask
    if (cmd == "pipx" and first == "install") or (cmd == "uv" and list(args[:2]) == ["tool", "install"]):
        return ask
    if cmd in ("brew", "port", "apt", "apt-get", "dnf", "yum", "snap", "choco", "winget") and first in (
            "install", "upgrade", "reinstall", "uninstall", "remove", "tap"):
        return ask
    if cmd in ("gem", "cargo", "go") and first == "install":
        return ask
    return []


def _rm(args: Sequence[str], cwd: Optional[str], repo_root: Optional[str]) -> List[Decision]:
    short = _short_flags(args)
    recursive = "--recursive" in args or bool(short & set("rR"))
    pos = _positionals(args)
    for p in pos:
        if recursive and p in _BROAD_RM:
            return [(DENY, f"`rm -r {p}` would delete far more than a scratch folder. Delete specific paths only.")]
        if recursive and repo_root:
            target = os.path.normpath(p if os.path.isabs(p) else os.path.join(cwd or repo_root, p))
            root = os.path.normpath(repo_root)
            if root == target or root.startswith(target.rstrip(os.sep) + os.sep):
                return [(DENY, "This `rm -r` deletes the repository (or a folder above it).")]
        if recursive:
            hit = _ancestor_of_protected(p)
            if hit:
                return [(ASK, REASON_PROTECTED.format(path=hit))]
    return []


def _grep_paths(args: Sequence[str]) -> List[str]:
    """Positional file operands of grep/rg (the first positional is the pattern unless -e/-f gives one)."""
    explicit = any(a in ("-e", "-f", "--regexp", "--file") or a.startswith(("--regexp=", "--file=")) for a in args)
    pos = _positionals(args, with_value=("-e", "-f", "--regexp", "--file", "-g", "--glob", "-t", "--type", "-m",
                                         "--max-count", "-A", "-B", "-C", "--context"))
    return pos if explicit else pos[1:]


def _segment(words: List[str], outs: List[str], others: List[str], raw: List[str], cwd: Optional[str],
             repo_root: Optional[str], depth: int) -> List[Decision]:
    found: List[Decision] = []
    cmd_words, nested = _strip_prefixes(words)
    for text in nested:
        found += analyze(text, cwd, repo_root, depth + 1)
    if not cmd_words:
        # Only assignments, for example DATABASE_PATH=./sehat.db: still checked below.
        cmd = ""
        args: List[str] = []
    else:
        cmd, args = _base(cmd_words[0]), cmd_words[1:]

    if cmd == "sudo":
        return [(DENY, "`sudo` is not used in this repo: no system changes from an agent session.")]
    if cmd in _SHELLS:
        for k, a in enumerate(args):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) and k + 1 < len(args):
                found += analyze(args[k + 1], cwd, repo_root, depth + 1)
                break
    if cmd == "eval" and args:
        found += analyze(" ".join(args), cwd, repo_root, depth + 1)
    if cmd == "git":
        found += _git(cmd_words)
    found += _installs(cmd, args)
    if cmd == "rm":
        found += _rm(args, cwd, repo_root)

    # Secrets and the shared database. Search commands only count real file operands; listing commands are fine.
    git_sub = _git_split(cmd_words)[0] if cmd == "git" else ""
    if cmd in _SEARCH:
        operands = _grep_paths(args) + others + outs
        if any(is_env_file(p) for p in operands):
            found.append((DENY, REASON_ENV))
        if any(is_shared_db(p) for p in operands):
            found.append((DENY, REASON_DB))
    elif cmd not in _METADATA:
        texts = raw
        if not (cmd == "git" and git_sub in _GIT_SAFE_FOR_ENV) and any(_ENV_IN_TEXT.search(t) for t in texts):
            found.append((DENY, REASON_ENV))
        if cmd != "git" and any(_DB_IN_TEXT.search(t) for t in texts):
            found.append((DENY, REASON_DB))

    # Changes to safety-critical files through the shell (the edit hook cannot see these).
    targets: List[str] = list(outs)
    if cmd in _MUTATING_ALL:
        targets += _positionals(args)
    elif cmd in _MUTATING_LAST:
        pos = _positionals(args)
        targets += pos[-1:]
    elif cmd in ("sed", "perl", "ruby") and any(a == "--in-place" or re.fullmatch(r"-[A-Za-z]*i.*", a) for a in args):
        targets += [a for a in args if not a.startswith("-")]
    for t in targets:
        if is_env_file(t):
            found.append((DENY, REASON_ENV))
        elif is_shared_db(t):
            found.append((DENY, REASON_DB))
        else:
            reason = protected_reason(t)
            if reason:
                found.append((ASK, reason))
    return found


_FALLBACK: Tuple[Tuple[str, str], ...] = (
    (r"\bgit\b[^\n;|&]*\badd\b[^\n;|&]*\s(-A|--all|-u|-f|\.)(\s|$)", "Stage files by name; broad `git add` is not used (AGENTS.md)."),
    (r"\bgit\b[^\n;|&]*\b(reset|clean)\b", "`git reset` and `git clean` are not used in this repo (AGENTS.md)."),
    (r"\bgit\b[^\n;|&]*\bstash\b(?!\s+(list|show)\b)", "`git stash` is not used in this repo (AGENTS.md)."),
    (r"\bgit\b[^\n;|&]*\bpush\b[^\n;|&]*(\s-f\b|--force|\s\+)", "Force pushes are not used in this repo (AGENTS.md)."),
    (r"(^|[\s;|&(])sudo\b", "`sudo` is not used in this repo."),
)


def _fallback(command: str) -> List[Decision]:
    """Unparseable text (for example unbalanced quotes): a conservative scan for the worst patterns."""
    text = strip_heredocs(command)
    found = [(DENY, reason) for pattern, reason in _FALLBACK if re.search(pattern, text)]
    if _ENV_IN_TEXT.search(text):
        found.append((DENY, REASON_ENV))
    if _DB_IN_TEXT.search(text):
        found.append((DENY, REASON_DB))
    return found


def analyze(command: str, cwd: Optional[str] = None, repo_root: Optional[str] = None, depth: int = 0) -> List[Decision]:
    """Every decision for a shell command (empty = no objection; the normal permission flow continues)."""
    if depth > 3 or not command.strip():
        return []
    tokens = tokenize(command)
    if tokens is None:
        return _fallback(command)
    found: List[Decision] = []
    fetching = False
    for sep, seg in _segments(tokens):
        words, outs, others = _split_redirects(seg)
        stripped, _ = _strip_prefixes(words)
        cmd = _base(stripped[0]) if stripped else ""
        if sep != "|":
            fetching = False
        if sep == "|" and fetching and cmd in _INTERPRETERS:
            found.append((DENY, "Piping a download into a shell or interpreter runs unreviewed code. Download, read, then decide."))
        fetching = fetching or cmd in ("curl", "wget")
        found += _segment(words, outs, others, seg, cwd, repo_root, depth)
    return found


def bash_decision(command: str, cwd: Optional[str] = None, repo_root: Optional[str] = None) -> Optional[Decision]:
    """The strongest decision for a shell command: deny beats ask; None means no objection."""
    found = analyze(command, cwd, repo_root)
    for level in (DENY, ASK):
        reasons = [r for d, r in found if d == level]
        if reasons:
            return level, " | ".join(dict.fromkeys(reasons))
    return None
