#!/usr/bin/env python3
"""
Auth server para el Reproductor Pasión de Gavilanes.
Login seguro con bcrypt + JWT en cookie httpOnly.

Variables de entorno (configurar en EasyPanel):
  ADMIN_USER      — usuario (default: admin)
  ADMIN_PASS      — contraseña en texto plano (default: Gavilanes2024!)
                    Se hashea con bcrypt al arrancar.
  SECRET_KEY      — clave JWT (default: genera una aleatoria)
  JWT_DAYS        — días de sesión (default: 7)
"""

import os
import secrets
import datetime
from pathlib import Path

import bcrypt
import jwt
from flask import (
    Flask, request, redirect, url_for,
    make_response, render_template_string,
    send_from_directory, abort
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
app = Flask(__name__)

SECRET_KEY  = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ADMIN_USER  = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS  = os.environ.get("ADMIN_PASS", "Gavilanes2024!")
JWT_DAYS    = int(os.environ.get("JWT_DAYS", "7"))
COOKIE_NAME = "gv_session"

# Hash de contraseña al arrancar (nunca en texto plano en memoria por más tiempo)
_PASS_HASH = bcrypt.hashpw(ADMIN_PASS.encode(), bcrypt.gensalt())

# Rate limiter — máximo 10 intentos de login por minuto por IP
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

STATIC_DIR = Path(__file__).parent / "static"

# ─────────────────────────────────────────
# HELPERS JWT
# ─────────────────────────────────────────

def make_token(username: str) -> str:
    payload = {
        "sub": username,
        "iat": datetime.datetime.utcnow(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=JWT_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_token(token: str):  # -> Optional[dict]
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None


def get_current_user():
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_token(token)


def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────
# PÁGINAS
# ─────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pasión de Gavilanes — Acceso</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Sans:wght@300;400;500;600&display=swap');

  :root {
    --bg:      #09090f;
    --surface: #111118;
    --border:  #28283a;
    --accent:  #c8a355;
    --red:     #8b1a1a;
    --text:    #eae6da;
    --muted:   #6a6a88;
    --error:   #e05555;
    --success: #3ecf8e;
  }

  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
  }

  /* Fondo animado */
  .bg-orb {
    position: fixed;
    border-radius: 50%;
    filter: blur(80px);
    opacity: 0.12;
    pointer-events: none;
    animation: drift 12s ease-in-out infinite alternate;
  }
  .bg-orb-1 { width: 500px; height: 500px; background: var(--accent); top: -150px; left: -150px; animation-delay: 0s; }
  .bg-orb-2 { width: 400px; height: 400px; background: var(--red);    bottom: -100px; right: -100px; animation-delay: -6s; }
  @keyframes drift {
    from { transform: translate(0, 0) scale(1); }
    to   { transform: translate(40px, 30px) scale(1.1); }
  }

  .card {
    position: relative;
    z-index: 10;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 48px 44px 40px;
    width: 100%;
    max-width: 420px;
    box-shadow: 0 32px 80px rgba(0,0,0,0.6);
    backdrop-filter: blur(20px);
    animation: slideUp 0.4s cubic-bezier(0.34,1.2,0.64,1);
  }
  @keyframes slideUp {
    from { opacity: 0; transform: translateY(24px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .logo {
    font-family: 'Playfair Display', serif;
    font-size: 1.6rem;
    font-weight: 900;
    color: var(--accent);
    text-align: center;
    margin-bottom: 6px;
    text-shadow: 0 0 40px rgba(200,163,85,0.3);
  }
  .logo span { color: var(--red); }

  .subtitle {
    text-align: center;
    font-size: 0.8rem;
    color: var(--muted);
    margin-bottom: 36px;
    letter-spacing: 0.04em;
  }

  .form-group {
    margin-bottom: 18px;
  }
  label {
    display: block;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin-bottom: 7px;
    font-weight: 500;
  }
  input {
    width: 100%;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 16px;
    color: var(--text);
    font-family: inherit;
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
  }
  input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(200,163,85,0.12);
  }
  input::placeholder { color: var(--muted); }

  .show-pass {
    position: relative;
  }
  .show-pass input { padding-right: 44px; }
  .toggle-pass {
    position: absolute;
    right: 14px;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: var(--muted);
    cursor: pointer;
    font-size: 1rem;
    padding: 2px;
    transition: color 0.15s;
  }
  .toggle-pass:hover { color: var(--text); }

  .btn-login {
    width: 100%;
    padding: 13px;
    border-radius: 10px;
    border: none;
    background: linear-gradient(135deg, var(--accent) 0%, #a07a30 100%);
    color: #000;
    font-family: inherit;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
    transition: all 0.2s;
    letter-spacing: 0.02em;
    position: relative;
    overflow: hidden;
  }
  .btn-login:hover   { transform: translateY(-1px); box-shadow: 0 8px 24px rgba(200,163,85,0.35); }
  .btn-login:active  { transform: translateY(0); }
  .btn-login:disabled { opacity: 0.6; cursor: default; transform: none; }

  /* Spinner dentro del botón */
  .btn-login .spinner {
    display: none;
    width: 16px; height: 16px;
    border: 2px solid rgba(0,0,0,0.3);
    border-top-color: #000;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    position: absolute;
    left: 50%; top: 50%;
    margin: -8px 0 0 -8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .btn-login.loading .btn-text { opacity: 0; }
  .btn-login.loading .spinner  { display: block; }

  .alert {
    padding: 11px 14px;
    border-radius: 9px;
    font-size: 0.82rem;
    margin-bottom: 18px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .alert-error   { background: rgba(224,85,85,0.1);  border: 1px solid rgba(224,85,85,0.3);  color: var(--error);   }
  .alert-success { background: rgba(62,207,142,0.1); border: 1px solid rgba(62,207,142,0.3); color: var(--success); }

  .footer-text {
    text-align: center;
    font-size: 0.7rem;
    color: var(--border);
    margin-top: 28px;
    letter-spacing: 0.06em;
  }
  .footer-text span { color: var(--muted); }

  @media (max-width: 480px) {
    .card { padding: 36px 24px 32px; margin: 16px; border-radius: 16px; }
  }
</style>
</head>
<body>

<div class="bg-orb bg-orb-1"></div>
<div class="bg-orb bg-orb-2"></div>

<div class="card">
  <div class="logo">🦅 Pasión de <span>Gavilanes</span></div>
  <div class="subtitle">Reproductor privado — Acceso restringido</div>

  {% if error %}
  <div class="alert alert-error">
    <span>🔒</span> {{ error }}
  </div>
  {% endif %}

  <form method="POST" action="/login" id="loginForm">
    <div class="form-group">
      <label for="username">Usuario</label>
      <input type="text" id="username" name="username"
             placeholder="Tu usuario" autocomplete="username"
             value="{{ username|default('') }}" required autofocus>
    </div>

    <div class="form-group">
      <label for="password">Contraseña</label>
      <div class="show-pass">
        <input type="password" id="password" name="password"
               placeholder="••••••••••" autocomplete="current-password" required>
        <button type="button" class="toggle-pass" onclick="togglePassword()" title="Mostrar/ocultar">
          <span id="eyeIcon">👁</span>
        </button>
      </div>
    </div>

    <button type="submit" class="btn-login" id="btnLogin">
      <span class="btn-text">Entrar</span>
      <div class="spinner"></div>
    </button>
  </form>

  <div class="footer-text">Sesión de <span>{{ jwt_days }} días</span> · Acceso seguro con JWT + bcrypt</div>
</div>

<script>
function togglePassword() {
  const input = document.getElementById('password');
  const icon  = document.getElementById('eyeIcon');
  if (input.type === 'password') {
    input.type = 'text';
    icon.textContent = '🙈';
  } else {
    input.type = 'password';
    icon.textContent = '👁';
  }
}
document.getElementById('loginForm').addEventListener('submit', function() {
  const btn = document.getElementById('btnLogin');
  btn.classList.add('loading');
  btn.disabled = true;
});
</script>
</body>
</html>"""


# ─────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login_page():
    if get_current_user():
        return redirect(url_for("player"))
    return render_template_string(LOGIN_HTML, error=None, jwt_days=JWT_DAYS)


@app.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").encode()

    # Verificar credenciales (tiempo constante para evitar timing attacks)
    user_ok = secrets.compare_digest(username.lower(), ADMIN_USER.lower())
    pass_ok = bcrypt.checkpw(password, _PASS_HASH)

    if not (user_ok and pass_ok):
        return render_template_string(
            LOGIN_HTML,
            error="Usuario o contraseña incorrectos.",
            username=username,
            jwt_days=JWT_DAYS
        ), 401

    token = make_token(username)
    resp  = make_response(redirect(url_for("player")))
    resp.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,            # inaccesible desde JS
        samesite="Lax",           # CSRF básico
        max_age=JWT_DAYS * 86400,
        secure=False,             # cambiar a True si usas HTTPS (EasyPanel con SSL)
    )
    return resp


@app.route("/logout")
def logout():
    resp = make_response(redirect(url_for("login_page")))
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.route("/")
@require_auth
def player():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/capitulos.json")
@require_auth
def capitulos_json():
    return send_from_directory(STATIC_DIR, "capitulos.json")


# Bloquear acceso directo a estáticos sin auth
@app.route("/static/<path:filename>")
def static_files(filename):
    if not get_current_user():
        abort(403)
    return send_from_directory(STATIC_DIR, filename)


# Seguridad: headers en todas las respuestas
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-XSS-Protection"]       = "1; mode=block"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    return response


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"  🦅 Reproductor auth server → http://0.0.0.0:{port}")
    print(f"  👤 Usuario: {ADMIN_USER}")
    print(f"  🔑 Contraseña: {'*' * len(ADMIN_PASS)}")
    print(f"  🕐 Sesión: {JWT_DAYS} días")
    app.run(host="0.0.0.0", port=port)
