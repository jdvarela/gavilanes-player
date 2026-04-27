FROM nginx:alpine

# Copiar archivos estáticos
COPY reproductor_gavilanes.html /usr/share/nginx/html/index.html
COPY capitulos.json /usr/share/nginx/html/capitulos.json

# Configuración nginx optimizada para SPA estática
RUN echo 'server { \
  listen 80; \
  root /usr/share/nginx/html; \
  index index.html; \
  gzip on; \
  gzip_types text/html application/json text/css application/javascript; \
  location / { try_files $uri $uri/ /index.html; } \
  location ~* \.(html|json)$ { add_header Cache-Control "no-cache"; } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
