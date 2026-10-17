FROM nginx:stable-alpine
COPY deploy/aws/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/src/ /usr/share/nginx/html/
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
