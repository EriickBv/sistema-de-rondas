from app.models import Guardia
import json

def test_crear_guardia(client):
    # 1. Enviamos petición para crear
    response = client.post('/api/auth/crear_guardia', json={
        "nombre": "Test Guardia",
        "rut": "1-9",
        "email": "test@guardia.com",
        "password": "123"
    })
    
    # 2. Verificamos que responda 201 (Created)
    assert response.status_code == 201
    assert b"Guardia creado" in response.data

def test_no_duplicar_rut(client):
    # Creamos uno primero
    client.post('/api/auth/crear_guardia', json={
        "nombre": "G1", "rut": "1-9", "email": "g1@mail.com", "password": "123"
    })
    
    # Intentamos crear otro con el MISMO RUT
    response = client.post('/api/auth/crear_guardia', json={
        "nombre": "G2", "rut": "1-9", "email": "g2@mail.com", "password": "123"
    })
    
    # Debe fallar (400 Bad Request)
    assert response.status_code == 400
    assert b"El RUT ya existe" in response.data

def test_login_exitoso(client):
    # Setup: Crear usuario
    client.post('/api/auth/crear_guardia', json={
        "nombre": "Login Man", "rut": "5-5", "email": "log@in.com", "password": "secure"
    })
    
    # Test: Intentar loguearse
    response = client.post('/api/auth/login_guardia', json={
        "rut": "5-5",
        "password": "secure"
    })
    
    data = json.loads(response.data)
    
    assert response.status_code == 200
    assert "token" in data  # Debe devolver un token

def test_login_fallido(client):
    # Setup: Crear usuario
    client.post('/api/auth/crear_guardia', json={
        "nombre": "Hacker Target", "rut": "6-6", "email": "hack@me.com", "password": "123"
    })
    
    # Test: Password incorrecta
    response = client.post('/api/auth/login_guardia', json={
        "rut": "6-6",
        "password": "INCORRECTA"
    })
    
    assert response.status_code == 401