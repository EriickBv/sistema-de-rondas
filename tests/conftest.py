import pytest
from app import create_app, db

@pytest.fixture
def app():
    # 1. Creamos la app con la config de TESTING (la del muro de seguridad)
    app = create_app('testing')
    
    with app.app_context():
        # 2. Creamos las tablas en la base de datos de MEMORIA (RAM)
        db.create_all()
        
        yield app  # Aquí corren los tests
        
        # 3. Al terminar, borramos la base de datos de MEMORIA
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def runner(app):
    return app.test_cli_runner()