# syntax=docker/dockerfile:1
FROM redis:7.4.11-alpine
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then cp /etc/ssl/certs/ca-certificates.crt /tmp/ca.bak; cat /run/secrets/extra_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    apk upgrade --no-cache; rc=$?; \
    if [ -f /tmp/ca.bak ]; then mv /tmp/ca.bak /etc/ssl/certs/ca-certificates.crt; fi; exit $rc
