# Deployment Workflow

- Make all code changes in this repository first; do not edit the production server as the primary source of truth.
- Before deployment, run focused validation, review the diff, commit the change, and push the exact commit to GitHub.
- Deploy only by having the server pull the verified GitHub commit, then build/restart the affected services and run health checks.
- Emergency server-only changes must be immediately copied back, reviewed, committed, and pushed before further work.
