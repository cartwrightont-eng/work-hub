import os
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from . import auth, people, deliverables, dashboard, overview, goals, documents, admin, review, work

def create_app(testing=False):
    app = Flask(__name__)
    app.config.update(
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],       # no default: fail loudly
        JWT_TOKEN_LOCATION=["cookies"],
        JWT_COOKIE_SECURE=not testing and os.environ.get("COOKIE_SECURE", "1") != "0",
        JWT_COOKIE_CSRF_PROTECT=True,
        JWT_COOKIE_SAMESITE="Lax",
        RATELIMIT_ENABLED=not testing,
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,                  # hard ceiling; each organisation sets its own lower limit
        TESTING=testing)
    hops = int(os.environ.get("PROXY_HOPS", "0"))          # 0 unless a trusted proxy (Render) sits in front
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)
    JWTManager(app)
    auth.limiter.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(people.bp)
    app.register_blueprint(deliverables.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(overview.bp)
    app.register_blueprint(goals.bp)
    app.register_blueprint(documents.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(review.bp)
    app.register_blueprint(work.bp)

    @app.get("/healthz")
    def healthz():
        from .db import db_cursor
        with db_cursor(commit=False) as cur: cur.execute("SELECT 1")
        return jsonify(ok=True)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not testing: resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.errorhandler(413)
    def too_big(_): return jsonify(error="file too large"), 413

    @app.errorhandler(429)
    def too_many(_): return jsonify(error="too many attempts"), 429
    return app
