def register_jobs(scheduler):
    @scheduler.task(
        'cron', id='reporte_diario',
        hour=7, minute=0,
        misfire_grace_time=3600  # Si el servidor estuvo caído, lo reintenta hasta 1h después
    )
    def reporte_diario():
        from app.api.reportes import enviar_reporte_diario
        app = getattr(scheduler, 'app', None)
        if app is None:
            import logging
            logging.getLogger(__name__).error(
                "scheduler.app no está definida — no se puede ejecutar reporte_diario"
            )
            return
        with app.app_context():
            try:
                enviar_reporte_diario()
            except Exception:
                app.logger.exception("Falló reporte_diario")