#!/bin/sh
# Runs before the main process (CMD).
# 1. Resolves and exports CMDSTAN so Prophet's cmdstanpy backend can find it.
# 2. Applies any pending Alembic migrations.
# 3. Execs CMD (uvicorn).
set -e

# ---------------------------------------------------------------------------
# CmdStan path resolution
# If the image was built with INSTALL_CMDSTAN=true, the binary tree lives
# under ~/.cmdstan/cmdstan-X.Y.Z/.  Discover the exact path at startup so
# cmdstanpy doesn't have to scan the filesystem on every model load.
# ---------------------------------------------------------------------------
if [ -z "${CMDSTAN}" ]; then
    _CMDSTAN_PATH=$(ls -d "${HOME}/.cmdstan/cmdstan-"* 2>/dev/null | sort | tail -1)
    if [ -n "${_CMDSTAN_PATH}" ]; then
        export CMDSTAN="${_CMDSTAN_PATH}"
        echo "[entrypoint] CMDSTAN=${CMDSTAN}"
    fi
fi

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------
echo "[entrypoint] running database migrations..."
alembic upgrade head

# ---------------------------------------------------------------------------
# Hand off to CMD
# ---------------------------------------------------------------------------
echo "[entrypoint] starting application..."
exec "$@"
