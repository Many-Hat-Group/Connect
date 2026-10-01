# Hardened wrappers for upstream images

Each wrapper is the pinned official image plus that distribution's current
security updates, rebuilt by `install.sh` and `make build`. Nothing else is
changed. `scripts/scan-images.sh` (Trivy) checks the results.

The optional `extra_ca` build secret is only for builders behind a TLS
intercepting proxy; it is appended to the trust store for that RUN step and
removed again, so it never ends up in the image.
