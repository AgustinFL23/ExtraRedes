from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Router(db.Model):
    __tablename__ = 'routers'
    
    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column(db.String(100), nullable=True)
    ip_address = db.Column(db.String(50), nullable=False, unique=True)
    location = db.Column(db.String(255), nullable=True)
    contact = db.Column(db.String(255), nullable=True)
    uptime = db.Column(db.String(100), nullable=True)
    os_version = db.Column(db.String(255), nullable=True)
    hardware = db.Column(db.String(255), nullable=True)
    
    interfaces = db.relationship('Interface', backref='router', lazy=True, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'<Router {self.hostname or self.ip_address}>'

class Interface(db.Model):
    __tablename__ = 'interfaces'
    
    id = db.Column(db.Integer, primary_key=True)
    router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=True) # up, down
    ip_address = db.Column(db.String(50), nullable=True)
    mask = db.Column(db.String(50), nullable=True)
    connected_to = db.Column(db.String(100), nullable=True)

class Alert(db.Model):
    __tablename__ = 'alerts'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    event_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), nullable=False) # info, warning, critical

class Link(db.Model):
    __tablename__ = 'links'
    
    id = db.Column(db.Integer, primary_key=True)
    source_router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), nullable=False)
    target_router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), nullable=False)
    source_interface = db.Column(db.String(100), nullable=False)
    target_interface = db.Column(db.String(100), nullable=False)
