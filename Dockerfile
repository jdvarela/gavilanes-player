# ─────────────────────────────────────────
# Reproductor Pasión de Gavilanes
# Auth: Flask + bcrypt + JWT
# ─────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias Python
COPY auth/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar backend
COPY auth/app.py .

# Copiar archivos estáticos protegidos
RUN mkdir -p static
COPY reproductor_gavilanes.html      static/index.html
COPY capitulos_gavilanes.json        static/capitulos_gavilanes.json
COPY capitulos_bella.json            static/capitulos_bella.json

# Variables de entorno por defecto (CAMBIAR EN EASYPANEL)
ENV ADMIN_USER=admin \
    ADMIN_PASS=Gavilanes2024! \
    JWT_DAYS=7 \
    PORT=5000

EXPOSE 5000

# Gunicorn: producción, 2 workers, timeout 120s
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "--access-logfile", "-"]
