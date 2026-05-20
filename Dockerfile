# =============================================================================
# Stage 1 — builder
# Installs all Python deps (including C-extension builds) and downloads
# CmdStan (required by Prophet at runtime).  Nothing from this stage ends up
# in the final image except the installed files we explicitly COPY.
# =============================================================================
FROM python:3.12-slim AS builder

# Build tools needed to compile native extensions (psycopg2, scikit-learn …)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install deps into a prefix directory so the final stage can copy them
# cleanly, without any build-time tools.
COPY pyproject.toml .
RUN pip install --upgrade pip --quiet \
 && pip install --no-cache-dir --prefix=/install .

# Download and install CmdStan (pre-compiled binaries, ~200 MB).
# Prophet's cmdstanpy backend needs this at runtime.
# The default install path is ~/.cmdstan/; we redirect to /opt/cmdstan so
# we can copy it into the final image in a predictable location.
RUN python - <<'EOF'
import cmdstanpy, pathlib
cmdstanpy.install_cmdstan(dir="/opt/cmdstan", overwrite=False, verbose=True)
# Write the exact versioned path so the final stage can set CMDSTAN correctly.
versioned = sorted(pathlib.Path("/opt/cmdstan").glob("cmdstan-*"))[-1]
pathlib.Path("/opt/cmdstan/.cmdstan_version_path").write_text(str(versioned))
EOF


# =============================================================================
# Stage 2 — final
# Minimal runtime image.  Runs as a non-root user.
# =============================================================================
FROM python:3.12-slim AS final

# Runtime libraries only — no compilers
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN adduser --disabled-password --gecos "" appuser

# ---- Python packages --------------------------------------------------------
COPY --from=builder /install /usr/local

# ---- CmdStan binaries -------------------------------------------------------
# Copy the entire cmdstan directory tree; cmdstanpy auto-discovers it under
# ~/.cmdstan/ when CMDSTAN env var is not set.
COPY --from=builder /opt/cmdstan /home/appuser/.cmdstan

# docker-entrypoint.sh resolves the exact versioned cmdstan path at startup
# and exports CMDSTAN before uvicorn loads Prophet models.
RUN chown -R appuser:appuser /home/appuser/.cmdstan

# ---- Application code -------------------------------------------------------
WORKDIR /app

COPY --chown=appuser:appuser alembic/       alembic/
COPY --chown=appuser:appuser alembic.ini    alembic.ini
COPY --chown=appuser:appuser app/           app/
COPY --chown=appuser:appuser docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Writable directory for runtime-trained models; mount a volume in production.
RUN mkdir -p data/models && chown -R appuser:appuser data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:8000/health', timeout=8); \
         sys.exit(0 if r.status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info"]
