FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
# filter.txt lives on a shared volume at /data so the backend can update it and
# reload us with SIGHUP. Start it empty-safe if the volume has no file yet.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
