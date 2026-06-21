# Production safety

- Never run destructive SQL against production without a reviewed plan.
- Schema changes go through `backend/schema/intel_foundation.sql` and the k8s schema migrate job.
- Secrets live in `/root/.zivo/secrets.env` on the VPS — never commit them.
