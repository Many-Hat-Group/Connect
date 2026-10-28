# Notes for AI agents working in this repository

- **Deploying to a Proxmox LXC:** follow `docs/DEPLOY-LXC.md` step by step.
- Day-to-day operations: `docs/RUNBOOK.md`. Architecture: `docs/ARCHITECTURE.md`. Build record: `docs/AS-BUILT.md`.
- Never edit anything under `vendor/` (pinned upstream code). `./scripts/check-vendor.sh` must pass.
- Brand-specific values live only in `app/defaults.yaml` and `.env.example` (`./scripts/check-brand.sh`).
- Never print or commit `.env` or any secret.
- Anything outside the LXC (edge proxy, DNS, firewall, Proxmox) needs the owner's approval; making the service public needs two explicit confirmations.
- Tests: `make test-unit test-integration` (needs `make test-deps`), browser tests `make e2e-up test-e2e`, Phase 0 VDO.Ninja checks `make phase0-up test-phase0`.
