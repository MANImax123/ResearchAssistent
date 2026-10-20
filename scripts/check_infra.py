#!/usr/bin/env python3
"""
ResearchFlow Infrastructure Health Check

Verifies that all required services are running and accessible:
- PostgreSQL (with pgvector extension)
- Redis
- MinIO (with bucket)
- Neo4j
- GROBID

Usage:
    python scripts/check_infra.py

Reads connection details from .env file or environment variables.
"""

import os
import sys
import socket
import subprocess
import urllib.request
import urllib.error
from pathlib import Path


# ─── Load .env file ───────────────────────────────────────────────────
def load_dotenv(env_path: str = ".env") -> None:
    """Simple .env loader (no external dependencies required)."""
    path = Path(env_path)
    if not path.exists():
        # Try .env.example as fallback
        path = Path(".env.example")
        if not path.exists():
            print("⚠  No .env or .env.example found, using defaults")
            return
        print(f"ℹ  Using {path} (copy to .env and set real values for production)")

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if not os.environ.get(key):
                    os.environ[key] = value


def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# .env uses Docker service names; from the host they are reachable on localhost
DOCKER_SERVICE_HOSTS = {"postgres", "redis", "minio", "neo4j", "grobid"}


def host_for(host: str) -> str:
    return "localhost" if host in DOCKER_SERVICE_HOSTS else host


# ─── Check functions ──────────────────────────────────────────────────
def check_postgres() -> tuple[bool, str]:
    """Check PostgreSQL accepts TCP connections and answers queries."""
    try:
        host = host_for(get_env("POSTGRES_HOST", "localhost"))
        port = int(get_env("POSTGRES_PORT", "5432"))
        with socket.create_connection((host, port), timeout=5):
            pass
        out = run_psql("SELECT version();")
        return True, f"Connected — {out.split(',')[0]}"
    except Exception as e:
        return False, str(e)


def check_pgvector() -> tuple[bool, str]:
    """Ensure the pgvector extension is installed and active."""
    try:
        version = run_psql(
            "CREATE EXTENSION IF NOT EXISTS vector; "
            "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
        )
        if not version:
            return False, "Extension 'vector' not found"
        return True, f"Extension active (v{version.splitlines()[-1].strip()})"
    except Exception as e:
        return False, str(e)


def run_psql(sql: str) -> str:
    """Run SQL inside the postgres container (no psycopg needed on the host)."""
    cmd = [
        "docker", "exec", "researchflow-postgres",
        "psql", "-U", get_env("POSTGRES_USER", "researchflow"),
        "-d", get_env("POSTGRES_DB", "researchflow"), "-t", "-A", "-q", "-c", sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "psql failed")
    return proc.stdout.strip()


def check_redis() -> tuple[bool, str]:
    """Check Redis connectivity."""
    try:
        redis_url = get_env("REDIS_URL", "redis://localhost:6379/0")
        # Parse host/port from URL
        # Format: redis://host:port/db
        parts = redis_url.replace("redis://", "").split("/")[0]
        host, _, port = parts.partition(":")
        host = host or "localhost"
        port = port or "6379"

        host = host_for(host)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, int(port)))

        if result != 0:
            sock.close()
            return False, f"Cannot connect to {host}:{port}"

        # Send PING
        sock.send(b"PING\r\n")
        response = sock.recv(64).decode()
        sock.close()

        if "+PONG" in response:
            return True, "Connected, PONG received"
        return False, f"Unexpected response: {response}"

    except Exception as e:
        return False, str(e)


def check_minio() -> tuple[bool, str]:
    """Check MinIO liveness and that the project bucket exists."""
    try:
        host, _, port = get_env("MINIO_ENDPOINT", "localhost:9000").partition(":")
        endpoint = f"{host_for(host)}:{port or '9000'}"
        bucket = get_env("MINIO_BUCKET", "researchflow")

        with urllib.request.urlopen(f"http://{endpoint}/minio/health/live", timeout=5) as resp:
            if resp.status != 200:
                return False, f"Health endpoint returned {resp.status}"

        # Anonymous HEAD: 403 = bucket exists (private), 404 = missing
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://{endpoint}/{bucket}", method="HEAD"), timeout=5)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False, f"Healthy, but bucket '{bucket}' does not exist"
            if e.code != 403:
                return False, f"Bucket check failed: HTTP {e.code}"
        return True, f"Healthy, bucket '{bucket}' exists"
    except Exception as e:
        return False, str(e)


def check_neo4j() -> tuple[bool, str]:
    """Check Neo4j connectivity."""
    try:
        # Neo4j browser runs on port 7474 (HTTP)
        url = "http://localhost:7474"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "researchflow-check")

        try:
            response = urllib.request.urlopen(req, timeout=10)
            if response.status == 200:
                return True, "Browser accessible on port 7474"
            return False, f"HTTP {response.status}"
        except urllib.error.HTTPError as e:
            if e.code in (200, 301, 302):
                return True, "Browser accessible"
            return False, f"HTTP {e.code}"

    except Exception as e:
        return False, str(e)


def check_grobid() -> tuple[bool, str]:
    """Check GROBID service."""
    try:
        grobid_url = get_env("GROBID_URL", "http://localhost:8070").replace("://grobid:", "://localhost:")

        url = f"{grobid_url}/api/isalive"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "researchflow-check")

        try:
            response = urllib.request.urlopen(req, timeout=15)
            if response.status == 200:
                return True, "Service alive and ready"
            return False, f"HTTP {response.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"

    except Exception as e:
        return False, str(e)


# ─── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    # Windows consoles default to cp1252, which can't print the status symbols
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("  ResearchFlow Infrastructure Health Check")
    print("=" * 60)
    print()

    # Load environment
    load_dotenv()

    checks = [
        ("PostgreSQL", check_postgres),
        ("pgvector", check_pgvector),
        ("Redis", check_redis),
        ("MinIO", check_minio),
        ("Neo4j", check_neo4j),
        ("GROBID", check_grobid),
    ]

    results = []
    all_ok = True

    for name, check_fn in checks:
        print(f"  Checking {name}...", end=" ", flush=True)
        ok, detail = check_fn()
        status = "OK" if ok else "FAIL"
        symbol = "✅" if ok else "❌"
        print(f"{symbol} {status} — {detail}")
        results.append((name, ok, detail))
        if not ok:
            all_ok = False

    print()
    print("-" * 60)
    if all_ok:
        print("  ✅ All services are healthy!")
    else:
        failed = [name for name, ok, _ in results if not ok]
        print(f"  ❌ Failed services: {', '.join(failed)}")
        print("  → Make sure Docker Compose is running: make up")
        print("  → Check logs: make logs")
    print("-" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
