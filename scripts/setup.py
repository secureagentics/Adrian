#!/usr/bin/env python3
"""Adrian bootstrap utility.

Runs inside the `adrian-setup` container. Initialises the SQLite
database, manages the admin row, and writes `.env`.

Usage (always invoked via docker run):
    docker run --rm -it -v "$(pwd):/workspace" adrian-setup <subcommand>

Subcommands:
    bootstrap          first-run init
    reset-password     replace the admin password
    set-model          switch the local GGUF in .env
    apply-migrations   apply any new migrations to an existing DB
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sqlite3
import sys
import uuid
from pathlib import Path


# ----------------------------------------------------------------
# Constants
# ----------------------------------------------------------------

DEFAULT_WORKSPACE = Path("/workspace")
MIGRATIONS_DIR = Path("/app/migrations")
ENV_TEMPLATE = Path("/app/.env.example")

PBKDF2_ITERATIONS = 600_000  # OWASP minimum for PBKDF2-SHA256.
PBKDF2_SALT_BYTES = 16
PBKDF2_KEY_LEN = 32
HASH_PREFIX = "pbkdf2_sha256"

DEFAULT_ADMIN_EMAIL = "admin@localhost"
DEFAULT_CTX_SIZE = 8192
DEFAULT_BACKEND_PORT = 8080
DEFAULT_DASHBOARD_PORT = 3000

# Local llama.cpp service URL inside the compose network. The `llm`
# service publishes a Chat Completions endpoint here; `bootstrap
# --gguf <name>` and `set-model --gguf <name>` wire the env to point
# at it so `docker compose --profile llm up -d` is all the operator
# needs to do next.
LOCAL_LLM_URL = "http://adrian-llm:8081/v1/chat/completions"

# Recommended on-device classifier: Gemma 4. Two variants ship as
# Q4_K_M GGUF re-uploads from the Unsloth team. `bootstrap` with no
# --gguf flag offers an interactive picker that downloads one of these
# into ./models/.
GEMMA_VARIANTS = {
    "E4B": {
        "filename": "gemma-4-E4B-it-Q4_K_M.gguf",
        "url": "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        "size_gb": 5.0,
        "vram_gb": 7,
        "tagline": "recommended",
    },
    "E2B": {
        "filename": "gemma-4-E2B-it-Q4_K_M.gguf",
        "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
        "size_gb": 3.1,
        "vram_gb": 5,
        "tagline": "smaller, faster",
    },
}
DEFAULT_VARIANT = "E4B"
NO_TRANSACTION_MARKER = "-- adrian: no-transaction"


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------


def hash_password(plaintext: str) -> str:
    """PBKDF2-SHA256 with a random salt. Returns the storage format
    `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` that the Go
    backend will verify."""
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        plaintext.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_KEY_LEN,
    )
    return f"{HASH_PREFIX}${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def generate_password() -> str:
    """16-char URL-safe random password."""
    return secrets.token_urlsafe(12)


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    """Apply previously-unseen `*.sql` files in lexical order.

    Applied filenames are recorded in `schema_migrations`, matching the
    Go backend runner. This keeps setup/bootstrap safe for future
    migrations that cannot be written as idempotent SQL.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name       TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """,
    )
    conn.commit()

    applied: list[str] = []
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        raise SystemExit(f"no migrations found in {migrations_dir}")
    for path in sql_files:
        if migration_applied(conn, path.name):
            continue
        sql = path.read_text(encoding="utf-8")
        if NO_TRANSACTION_MARKER in sql:
            try:
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (path.name,))
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                conn.execute("PRAGMA foreign_keys=ON")
                raise
        else:
            quoted_name = path.name.replace("'", "''")
            try:
                conn.executescript(
                    "BEGIN;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations (name) "
                    f"VALUES ('{quoted_name}');\n"
                    "COMMIT;\n",
                )
            except sqlite3.Error:
                conn.rollback()
                raise
        applied.append(path.name)
    return applied


def migration_applied(conn: sqlite3.Connection, name: str) -> bool:
    """Return True when `name` has already been recorded in the ledger."""
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?",
        (name,),
    ).fetchone()
    return row is not None


def read_env(env_path: Path) -> dict[str, str]:
    """Parse a `KEY=VALUE` env file. Comments and blanks ignored.
    Quoted values are stripped of surrounding double quotes."""
    if not env_path.exists():
        return {}
    parsed: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        parsed[key.strip()] = value
    return parsed


def write_env(env_path: Path, values: dict[str, str], template: Path) -> None:
    """Render `.env` from the template, overlaying `values`. Keys not
    in the template are appended at the end."""
    if not template.exists():
        # Fallback: write only the supplied values.
        env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in values.items()) + "\n",
            encoding="utf-8",
        )
        return

    out_lines: list[str] = []
    seen: set[str] = set()
    for line in template.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        if "=" not in stripped:
            out_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        seen.add(key)
        new_value = values.get(key, "")
        out_lines.append(f"{key}={new_value}")

    # Append any keys the template didn't know about.
    extras = sorted(k for k in values if k not in seen)
    if extras:
        out_lines.append("")
        out_lines.append("# Set by setup.py")
        for key in extras:
            out_lines.append(f"{key}={values[key]}")

    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def download_gguf(url: str, dest: Path) -> None:
    """Stream-download `url` to `dest` via stdlib urllib, printing
    a single carriage-return-updated progress line. Cleans up the
    partial file on any failure (Ctrl-C, network error, full disk)
    so a botched download doesn't get used by accident.
    """
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    req = urllib.request.Request(url, headers={"User-Agent": "adrian-setup/1.0"})
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (URL is in our constants)
            total = int(resp.headers.get("Content-Length", "0"))
            chunk_size = 4 * 1024 * 1024  # 4 MiB
            downloaded = 0
            with tmp.open("wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100.0 / total
                        sys.stdout.write(
                            f"\r  {downloaded / 1e9:5.2f} / "
                            f"{total / 1e9:5.2f} GB ({pct:5.1f}%)"
                        )
                        sys.stdout.flush()
        if total:
            sys.stdout.write("\n")
        tmp.replace(dest)
    except BaseException:
        # Includes KeyboardInterrupt; preserve the partial-cleanup
        # invariant on Ctrl-C as well as on network errors.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def prompt_default_gguf(models_dir: Path) -> str | None:
    """Interactive picker for the default classifier model.

    Returns the GGUF filename (under `models_dir`) the operator
    selected, after downloading if needed. Returns None when the
    operator skips, or when stdin isn't a TTY (so non-interactive
    callers don't hang on input).
    """
    if not sys.stdin.isatty():
        return None

    # If the operator already downloaded a GGUF before running
    # bootstrap, offer to use that instead of prompting again.
    existing = sorted(p for p in models_dir.glob("*.gguf") if p.is_file())
    if existing:
        sys.stdout.write("\nGGUFs already in ./models/:\n")
        for p in existing:
            size_gb = p.stat().st_size / 1e9
            sys.stdout.write(f"  {p.name}  ({size_gb:.1f} GB)\n")
        sys.stdout.write(f"\nUse {existing[0].name}? [Y/n]: ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans in ("", "y", "yes"):
            return existing[0].name
        # fall through to download-or-skip prompt

    sys.stdout.write(
        "\n"
        "Adrian recommends Gemma 4 for on-device classification.\n"
        "Two variants are available as Q4_K_M GGUFs:\n"
        "\n"
    )
    keys = list(GEMMA_VARIANTS.keys())
    for i, key in enumerate(keys, start=1):
        v = GEMMA_VARIANTS[key]
        sys.stdout.write(
            f"  [{i}] {key}  ~{v['size_gb']:.1f} GB on disk, "
            f"~{v['vram_gb']} GB VRAM  ({v['tagline']})\n"
        )
    sys.stdout.write(
        "  [s] skip, configure manually with --gguf\n"
        "\n"
        f"Choose [{keys.index(DEFAULT_VARIANT) + 1}]: "
    )
    sys.stdout.flush()

    ans = sys.stdin.readline().strip().lower()
    if ans == "s":
        return None
    if not ans:
        choice = DEFAULT_VARIANT
    elif ans.isdigit() and 1 <= int(ans) <= len(keys):
        choice = keys[int(ans) - 1]
    elif ans.upper() in GEMMA_VARIANTS:
        choice = ans.upper()
    else:
        sys.stderr.write(f"  unrecognised choice {ans!r}; skipping.\n")
        return None

    variant = GEMMA_VARIANTS[choice]
    dest = models_dir / variant["filename"]
    if dest.exists():
        sys.stdout.write(f"\n  {dest} already present, skipping download.\n")
        return variant["filename"]

    sys.stdout.write(
        f"\n  Downloading {variant['filename']} (~{variant['size_gb']:.1f} GB)\n"
        f"  from {variant['url']}\n"
    )
    try:
        download_gguf(variant["url"], dest)
    except KeyboardInterrupt:
        sys.stderr.write("\n  download cancelled.\n")
        return None
    except Exception as exc:
        sys.stderr.write(f"\n  download failed: {exc}\n")
        return None
    sys.stdout.write(f"  saved to {dest}\n")
    return variant["filename"]


def upsert_admin(conn: sqlite3.Connection, email: str, password_hash: str) -> bool:
    """INSERT or UPDATE the admin row. Returns True if inserted, False if updated."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (password_hash, row[0]),
        )
        conn.commit()
        return False
    cur.execute(
        """
        INSERT INTO users (id, email, name, role, password_hash, must_change_password)
        VALUES (?, ?, ?, 'admin', ?, 1)
        """,
        (str(uuid.uuid4()), email, "Administrator", password_hash),
    )
    conn.commit()
    return True


# ----------------------------------------------------------------
# Subcommand: bootstrap
# ----------------------------------------------------------------


def cmd_bootstrap(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    data_dir = workspace / "data"
    models_dir = workspace / "models"
    db_path = data_dir / "adrian.db"
    env_path = workspace / ".env"

    if not workspace.is_dir():
        sys.stderr.write(f"workspace not a directory: {workspace}\n")
        return 1
    if not os.access(workspace, os.W_OK):
        sys.stderr.write(f"workspace not writeable: {workspace}\n")
        return 1
    data_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    if db_path.exists() and not args.force:
        sys.stderr.write(
            f"database already exists: {db_path}\n"
            f"  - run `reset-password` to change the admin password;\n"
            f"  - run `apply-migrations` to apply schema updates;\n"
            f"  - or pass --force to wipe and re-create (destructive).\n"
        )
        return 1
    if db_path.exists() and args.force:
        if sys.stdin.isatty():
            confirm = input(f"--force will wipe {db_path}. Type 'yes' to confirm: ")
            if confirm.strip().lower() != "yes":
                sys.stderr.write("aborted.\n")
                return 1
        db_path.unlink()

    env_values = read_env(env_path)
    env_values.setdefault("ADRIAN_LLM_MODEL_PATH", "")
    env_values.setdefault("ADRIAN_LLM_CTX_SIZE", str(DEFAULT_CTX_SIZE))
    env_values.setdefault("ADRIAN_BACKEND_PORT", str(DEFAULT_BACKEND_PORT))
    env_values.setdefault("ADRIAN_DASHBOARD_PORT", str(DEFAULT_DASHBOARD_PORT))
    if not env_values.get("ADRIAN_SESSION_SECRET"):
        env_values["ADRIAN_SESSION_SECRET"] = secrets.token_hex(32)

    gguf_name = args.gguf or prompt_default_gguf(models_dir)
    if not gguf_name:
        sys.stderr.write(
            "no GGUF specified.\n"
            "  Pass --gguf <name> for a model under ./models/, or accept the\n"
            "  interactive picker's default to fetch Gemma 4 E4B.\n"
        )
        return 1
    gguf_path = models_dir / gguf_name
    if not gguf_path.exists():
        sys.stderr.write(
            f"GGUF file not found: {gguf_path}\n"
            f"  Place the model file under {models_dir} and re-run.\n"
        )
        return 1
    # The bundled llama.cpp container serves the GGUF at
    # http://adrian-llm:8081/v1/chat/completions; wire the env so
    # `docker compose --profile llm up -d` is all the operator needs
    # next. The local server doesn't enforce auth, so the bearer is a
    # placeholder.
    env_values["ADRIAN_LLM_URL"] = LOCAL_LLM_URL
    env_values["ADRIAN_LLM_API_KEY"] = "local-no-auth"
    env_values["ADRIAN_LLM_MODEL"] = "local"
    env_values["ADRIAN_LLM_MODEL_PATH"] = f"/models/{gguf_name}"
    llm_summary = f"local model: /models/{gguf_name} via {LOCAL_LLM_URL}"

    write_env(env_path, env_values, ENV_TEMPLATE)

    conn = open_db(db_path)
    applied = apply_migrations(conn, MIGRATIONS_DIR)

    password = args.password or generate_password()
    pw_hash = hash_password(password)
    inserted = upsert_admin(conn, args.admin_email, pw_hash)
    conn.close()

    # The `llm` service lives behind the `llm` profile, so the
    # operator must opt in for the bundled classifier to start.
    sys.stdout.write(
        f"\n"
        f"v Adrian bootstrap complete.\n"
        f"\n"
        f"  Database:    {db_path}\n"
        f"  Migrations:  {len(applied)} applied ({', '.join(applied)})\n"
        f"  Admin email: {args.admin_email}\n"
        f"  Admin pwd:   {password}\n"
        f"  LLM:         {llm_summary}\n"
        f"  Action:      {'inserted' if inserted else 'updated'} admin row\n"
        f"\n"
        f"  This password is shown once. Save it now; on next login\n"
        f"  the dashboard will prompt you to change it. If you lose\n"
        f"  it, run `adrian-setup reset-password` to mint a new one.\n"
        f"\n"
        f"Next:\n"
        f"  docker compose --profile llm up -d\n"
        f"  open http://localhost:{env_values['ADRIAN_DASHBOARD_PORT']}\n"
        f"\n"
    )
    return 0


# ----------------------------------------------------------------
# Subcommand: reset-password
# ----------------------------------------------------------------


def cmd_reset_password(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = workspace / "data" / "adrian.db"

    if not db_path.exists():
        sys.stderr.write(f"database not found: {db_path}\n  Run `bootstrap` first.\n")
        return 1

    password = args.password or generate_password()
    pw_hash = hash_password(password)

    conn = open_db(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE email = ?",
        (pw_hash, args.admin_email),
    )
    if cur.rowcount == 0:
        sys.stderr.write(f"no user with email {args.admin_email}; nothing to reset.\n")
        conn.close()
        return 1
    conn.commit()
    conn.close()

    sys.stdout.write(
        f"\n"
        f"v Password reset for {args.admin_email}.\n"
        f"\n"
        f"  New password: {password}\n"
        f"\n"
        f"  Shown once. Save it now; the dashboard will prompt for a\n"
        f"  change on next login. If lost, run reset-password again.\n"
        f"\n"
    )
    return 0


# ----------------------------------------------------------------
# Subcommand: set-model
# ----------------------------------------------------------------


def cmd_set_model(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    env_path = workspace / ".env"
    models_dir = workspace / "models"

    if not env_path.exists():
        sys.stderr.write(f"no .env at {env_path}; run `bootstrap` first.\n")
        return 1

    env_values = read_env(env_path)

    if not args.gguf:
        sys.stderr.write("pass --gguf <name>.\n")
        return 1
    gguf_path = models_dir / args.gguf
    if not gguf_path.exists():
        sys.stderr.write(f"GGUF file not found: {gguf_path}\n")
        return 1
    env_values["ADRIAN_LLM_URL"] = LOCAL_LLM_URL
    env_values["ADRIAN_LLM_API_KEY"] = "local-no-auth"
    env_values["ADRIAN_LLM_MODEL"] = "local"
    env_values["ADRIAN_LLM_MODEL_PATH"] = f"/models/{args.gguf}"
    summary = f"local model: /models/{args.gguf} via {LOCAL_LLM_URL}"

    if args.ctx_size:
        env_values["ADRIAN_LLM_CTX_SIZE"] = str(args.ctx_size)

    write_env(env_path, env_values, ENV_TEMPLATE)

    sys.stdout.write(
        f"\n"
        f"v Model configuration updated.\n"
        f"  {summary}\n"
        f"  ctx_size: {env_values['ADRIAN_LLM_CTX_SIZE']}\n"
        f"\n"
    )
    return 0


# ----------------------------------------------------------------
# Subcommand: apply-migrations
# ----------------------------------------------------------------


def cmd_apply_migrations(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = workspace / "data" / "adrian.db"

    if not db_path.exists():
        sys.stderr.write(f"database not found: {db_path}\n  Run `bootstrap` first.\n")
        return 1

    conn = open_db(db_path)
    applied = apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()

    sys.stdout.write(
        f"\n"
        f"v {len(applied)} new migration file(s) applied:\n"
        + "".join(f"    {name}\n" for name in applied)
        + "\n"
    )
    return 0


# ----------------------------------------------------------------
# CLI
# ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adrian-setup",
        description="Adrian bootstrap utility (runs inside the adrian-setup container).",
    )
    parser.add_argument(
        "--workspace",
        default=str(DEFAULT_WORKSPACE),
        help=f"workspace path (default: {DEFAULT_WORKSPACE}).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_boot = sub.add_parser("bootstrap", help="first-run initialisation")
    p_boot.add_argument("--gguf", default=None, help="GGUF filename under ./models/")
    p_boot.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
    p_boot.add_argument(
        "--password", default=None, help="explicit admin password (default: random)"
    )
    p_boot.add_argument(
        "--force", action="store_true", help="wipe an existing database and re-create"
    )
    p_boot.set_defaults(func=cmd_bootstrap)

    p_reset = sub.add_parser("reset-password", help="reset admin password")
    p_reset.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
    p_reset.add_argument(
        "--password", default=None, help="explicit new password (default: random)"
    )
    p_reset.set_defaults(func=cmd_reset_password)

    p_model = sub.add_parser("set-model", help="switch the local GGUF")
    p_model.add_argument("--gguf", default=None)
    p_model.add_argument("--ctx-size", type=int, default=None)
    p_model.set_defaults(func=cmd_set_model)

    p_migrate = sub.add_parser("apply-migrations", help="apply pending schema migrations")
    p_migrate.set_defaults(func=cmd_apply_migrations)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
