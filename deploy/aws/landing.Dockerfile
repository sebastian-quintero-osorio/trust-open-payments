FROM node:24-alpine AS build
WORKDIR /app
COPY landing/package.json landing/package-lock.json ./
RUN npm ci
COPY landing/ ./
ARG PUBLIC_SITE_URL
ARG PUBLIC_WAITLIST_ENDPOINT=/api/v1/waitlist
ENV PUBLIC_SITE_URL=$PUBLIC_SITE_URL PUBLIC_WAITLIST_ENDPOINT=$PUBLIC_WAITLIST_ENDPOINT
RUN npm run build

FROM nginx:stable-alpine
COPY deploy/aws/landing.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
