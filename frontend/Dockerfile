# Frontend image: nginx serving static files + /api reverse proxy to backend
FROM nginx:1.27-alpine

# Replace default config
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Static assets
COPY frontend/index.html         /usr/share/nginx/html/index.html
COPY frontend/app.js             /usr/share/nginx/html/app.js
COPY frontend/quatelio_logo.png  /usr/share/nginx/html/quatelio_logo.png

EXPOSE 80
