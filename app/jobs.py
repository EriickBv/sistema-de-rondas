# app/jobs.py
def register_jobs(scheduler):
    @scheduler.task(
        'cron', id='reporte_diario',
        hour=7, minute=0,
        misfire_grace_time=3600  # Si el servidor estuvo caído, lo reintenta hasta 1h después
    )
    def reporte_diario():
        from app.api.reportes import enviar_reporte_diario
        enviar_reporte_diario()