from flask import Flask
from config import config
from .extensions import db, cors, migrate, scheduler
from .web import web_bp
from .api.reportes import reportes_bp
from .api.sedes import sedes_bp

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    db.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})
    migrate.init_app(app, db)

    if config_name != 'testing':
        scheduler.init_app(app)
        from .jobs import register_jobs
        register_jobs(scheduler)
        if not scheduler.running:
            scheduler.start()

    from .api.auth import auth_bp
    from .api.rondas import rondas_bp
    from .api.zonas import zonas_bp

    app.register_blueprint(auth_bp,      url_prefix='/api/auth')
    app.register_blueprint(rondas_bp,    url_prefix='/api/rondas')
    app.register_blueprint(zonas_bp,     url_prefix='/api/zonas')
    app.register_blueprint(reportes_bp,  url_prefix='/api/reportes')
    app.register_blueprint(sedes_bp, url_prefix='/api/sedes')
    app.register_blueprint(web_bp)
    return app