FROM node:22-alpine AS build
WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY package.json package-lock.json ./
# Bump npm's retry tolerance above the conservative defaults
# (2 retries, short timeouts). On flaky networks the bare ``npm ci``
# can fail mid-stream with ECONNRESET while downloading one of the
# ~360 packages; a single retry usually clears it. Costs nothing on
# clean builds since these settings only fire on failure.
#
# ``--loglevel http`` makes npm print one line per registry fetch.
# Default ``notice`` is near-silent and looks hung during the
# multi-minute download phase; ``http`` gives continuous feedback
# without going as noisy as ``info``/``verbose``. Pair with
# ``docker compose build --progress=plain`` to see it stream.
RUN npm config set fetch-retries 5 \
 && npm config set fetch-retry-factor 2 \
 && npm config set fetch-retry-mintimeout 10000 \
 && npm config set fetch-retry-maxtimeout 60000 \
 && npm ci --prefer-offline --loglevel http

# Build the app.
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime

# nginxinc/nginx-unprivileged drops master + workers to user `nginx`
# (UID 101) and listens on 8080 by default. Switch to root briefly so
# we can install the entrypoint and chown the asset path so the
# in-place sed in docker-entrypoint.sh runs as nginx.
USER root
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh \
    && chown -R nginx:nginx /usr/share/nginx/html
USER nginx

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
