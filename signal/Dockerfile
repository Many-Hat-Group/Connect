# syntax=docker/dockerfile:1
# VDO.Ninja handshake (signalling) server.
# Runs the UNMODIFIED vdoninja_advanced.js from the pinned vendor/websocket_server
# submodule. Build context is the repository root.
FROM node:22.20.0-alpine

ENV NODE_ENV=production \
    PORT=8443

WORKDIR /srv/signal
COPY signal/package.json signal/package-lock.json ./
# The optional "extra_ca" build secret is only for builders behind a TLS
# intercepting proxy; it is a no-op on a normal install.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --omit=dev --ignore-scripts && npm cache clean --force

COPY vendor/websocket_server/vdoninja_advanced.js vendor/websocket_server/LICENSE ./

USER node
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "vdoninja_advanced.js"]
