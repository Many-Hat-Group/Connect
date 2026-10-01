# syntax=docker/dockerfile:1
# postgres pinned, plus security updates. gosu (a Go binary built with an old
# toolchain) is replaced by Alpine's su-exec, which takes the same arguments
# ("gosu user cmd"). Flattened into one layer so the old binary does not
# linger in a lower layer; the base image's config is restated.
FROM postgres:16.15-alpine AS build
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then cp /etc/ssl/certs/ca-certificates.crt /tmp/ca.bak; cat /run/secrets/extra_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    apk upgrade --no-cache && apk add --no-cache su-exec \
    && rm -f /usr/local/bin/gosu /usr/local/bin/su-exec && ln -s /sbin/su-exec /usr/local/bin/gosu; rc=$?; \
    if [ -f /tmp/ca.bak ]; then mv /tmp/ca.bak /etc/ssl/certs/ca-certificates.crt; fi; exit $rc

FROM scratch
COPY --from=build / /
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=en_US.utf8 PG_MAJOR=16 PG_VERSION=16.15 \
    PGDATA=/var/lib/postgresql/data
VOLUME ["/var/lib/postgresql/data"]
EXPOSE 5432
STOPSIGNAL SIGINT
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
