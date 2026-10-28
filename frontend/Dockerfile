# Pikirler web interface: Vite build served by nginx, which also proxies /api
# to the backend so the browser talks to one origin (session cookies + CSRF).
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# openssl issues the self-signed certificate for HTTPS on the local network.
RUN apk add --no-cache openssl
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/locations.conf /etc/nginx/pikirler/locations.conf
COPY docker/05-pikirler-lan-cert.sh /docker-entrypoint.d/05-pikirler-lan-cert.sh
RUN chmod +x /docker-entrypoint.d/05-pikirler-lan-cert.sh
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80 443
