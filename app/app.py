import os
from flask import Flask
from models import db

def create_app():
    app = Flask(__name__)
    
    # Configuración de la base de datos SQLite
    # Apunta a la carpeta /db que creamos un nivel arriba de /app
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, '..', 'db', 'network_monitor.db')
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Inicializar la base de datos con la app
    db.init_app(app)
    
    # Crear las tablas automáticamente si no existen
    with app.app_context():
        db.create_all()
        print(f"Base de datos verificada/creada en: {db_path}")
        
    @app.route('/')
    def index():
        return {"estado": "ok", "mensaje": "API del Monitor de Red activa"}
        
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
