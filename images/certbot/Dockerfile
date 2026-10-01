# syntax=docker/dockerfile:1
# certbot/dns-cloudflare pinned, plus security updates and pinned fixed Python
# packages. Build-only tools (/opt/certbot/tools: requirement pins, not
# installed code) and pip itself (it vendors old urllib3 and msgpack; certbot
# never needs it at runtime) are removed. The result is flattened into one layer so no
# replaced file lingers in a lower layer; the base image's config is restated.
FROM certbot/dns-cloudflare:v5.8.0 AS build
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then cp /etc/ssl/certs/ca-certificates.crt /tmp/ca.bak; cat /run/secrets/extra_ca >> /etc/ssl/certs/ca-certificates.crt; export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; fi; \
    apk upgrade --no-cache && rm -rf /opt/certbot/tools \
    && pip install --no-cache-dir "urllib3==2.8.0" "setuptools==84.0.0" "msgpack==1.2.1" \
    && python -m pip uninstall -y pip && rm -rf /usr/local/lib/python3.14/ensurepip/_bundled; rc=$?; \
    if [ -f /tmp/ca.bak ]; then mv /tmp/ca.bak /etc/ssl/certs/ca-certificates.crt; fi; exit $rc

FROM scratch
COPY --from=build / /
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin PYTHON_VERSION=3.14.7
WORKDIR /opt/certbot
VOLUME ["/etc/letsencrypt", "/var/lib/letsencrypt"]
ENTRYPOINT ["certbot"]
