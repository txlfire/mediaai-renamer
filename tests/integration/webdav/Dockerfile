FROM python:3.13.5-alpine3.22

RUN apk add --no-cache curl openssl \
    && pip install --no-cache-dir \
        WsgiDAV==4.3.3 \
        cheroot==10.0.1 \
        PyYAML==6.0.2

COPY entrypoint.sh /usr/local/bin/mediaai-webdav-entrypoint
RUN chmod +x /usr/local/bin/mediaai-webdav-entrypoint

HEALTHCHECK --interval=2s --timeout=3s --start-period=3s --retries=30 \
    CMD curl --cacert /certs/ca.crt --silent --show-error --output /dev/null https://localhost:9443/

ENTRYPOINT ["/usr/local/bin/mediaai-webdav-entrypoint"]
