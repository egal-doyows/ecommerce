# Docker (Optional)

Production deploys to DigitalOcean use **systemd + nginx + native packages** —
see `deployment/systemd/`, `deployment/nginx/`, and `deployment/scripts/`. The
canonical install path is `docs/deployment.md`.

These Docker artifacts are kept for:

- Local development if you prefer Docker over a venv.
- Future migration to a container orchestrator (DO App Platform, Kubernetes).
- CI pipelines that build a publishable image.

## Run locally

```bash
cd deployment/docker
docker compose up --build
```

The compose file uses the repo root as the build context, so the same
`.env` file at the project root is shared with native dev workflows.

## Build the image standalone

```bash
# From the repo root (not from this directory):
docker build -f deployment/docker/Dockerfile -t ecommerce .
```

If you don't need Docker, ignore this folder entirely.
