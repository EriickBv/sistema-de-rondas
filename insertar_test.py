from app import create_app
from app.extensions import db
from app.models import RegistroRonda, Guardia, PuntoControl
from datetime import datetime, timedelta

app = create_app()

with app.app_context():
    # 1. Buscamos el primer guardia y el primer punto disponibles
    guardia = Guardia.query.first()
    punto = PuntoControl.query.first()

    if not guardia or not punto:
        print("❌ Error: Debes tener al menos un Guardia y una Zona creados en el panel.")
    else:
        # 2. Definimos la fecha de AYER
        fecha_ayer = datetime.now() - timedelta(days=1)
        
        # 3. Creamos el registro falso
        test_ronda = RegistroRonda(
            id_guardia=guardia.id_guardia,
            id_punto=punto.id_punto,
            fecha_hora=fecha_ayer,
            lat_real=punto.latitud,
            long_real=punto.longitud,
            distancia_error=0.0,
            observacion="Registro de prueba para validar envío de correo."
        )
        
        db.session.add(test_ronda)
        db.session.commit()
        print(f"✅ Registro insertado con éxito para el guardia '{guardia.nombre}' con fecha {fecha_ayer.strftime('%Y-%m-%d')}")