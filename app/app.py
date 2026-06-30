import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from models import db, Router, Interface

def create_app():
    app = Flask(__name__)
    app.secret_key = 'super_secret_key_for_exam'
    
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, '..', 'db', 'network_monitor.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    
    with app.app_context():
        db.create_all()
        
    @app.route('/')
    def index():
        return render_template('index.html')
        
    @app.route('/discover', methods=['POST'])
    def discover():
        from modules.discovery import discover_network
        from modules.snmp_utils import get_router_info
        
        seed_ip = request.form.get('seed_ip')
        ssh_creds = {
            'user': request.form.get('ssh_user'),
            'password': request.form.get('ssh_pass')
        }
        snmp_creds = {
            'snmp_version': request.form.get('snmp_version'),
            'snmp_community': request.form.get('snmp_community'),
            'v3_user': request.form.get('v3_user'),
            'v3_auth': request.form.get('v3_auth'),
            'v3_priv': request.form.get('v3_priv'),
        }
        
        # Descubrimiento recursivo por CDP usando Netmiko en R1
        topology = discover_network(seed_ip, ssh_creds)
        
        if topology and len(topology['routers']) > 0:
            for r_node in topology['routers']:
                ip = r_node['ip']
                # Intentamos obtener info ampliada por SNMP (fallará para R2/R3 si no hay enrutamiento aún)
                snmp_info = get_router_info(ip, snmp_creds) or {}
                
                existing = Router.query.filter_by(ip_address=ip).first()
                if not existing:
                    new_r = Router(
                        hostname=snmp_info.get('hostname') or r_node['hostname'],
                        ip_address=ip,
                        hardware=snmp_info.get('hardware') or r_node['hardware'],
                        os_version=snmp_info.get('os_version', 'Unknown'),
                        uptime=snmp_info.get('uptime', '0'),
                        location=snmp_info.get('location', ''),
                        contact=snmp_info.get('contact', '')
                    )
                    db.session.add(new_r)
                else:
                    # Actualizar datos si el SNMP ya funciona
                    if snmp_info:
                        existing.uptime = snmp_info.get('uptime', existing.uptime)
                        existing.os_version = snmp_info.get('os_version', existing.os_version)
            
            db.session.commit()
            return render_template('index.html', alert_success=f"Topología descubierta: {len(topology['routers'])} nodos encontrados.")
        else:
            return render_template('index.html', alert_error="Fallo en el descubrimiento CDP en la IP Semilla.")
            
    @app.route('/routing', methods=['POST'])
    def configure_routing_route():
        from modules.routing import configure_routing_all
        from modules.discovery import discover_network
        
        protocol = request.form.get('protocol')
        seed_ip = request.form.get('seed_ip')
        ssh_creds = {
            'user': request.form.get('ssh_user'),
            'password': request.form.get('ssh_pass')
        }
        
        routers = Router.query.all()
        topology = None
        
        if not routers:
            # Descubrimiento al vuelo si no se ha descubierto antes
            topology = discover_network(seed_ip, ssh_creds)
            if not topology or not topology.get('routers'):
                return render_template('index.html', alert_error="Fallo al descubrir la red automáticamente antes de enrutar.")
        else:
            topology = {'routers': [{'hostname': r.hostname, 'ip': r.ip_address} for r in routers]}
            
        success, msg = configure_routing_all(protocol, seed_ip, ssh_creds, topology)
        
        if success:
            return render_template('index.html', alert_success=msg)
        else:
            return render_template('index.html', alert_error=msg)

    @app.route('/routers')
    def view_routers():
        routers = Router.query.all()
        return jsonify([{'id': r.id, 'hostname': r.hostname, 'ip': r.ip_address, 'hardware': r.hardware} for r in routers])
        
    @app.route('/routers/<int:router_id>/interfaces')
    def view_interfaces(router_id):
        interfaces = Interface.query.filter_by(router_id=router_id).all()
        return jsonify([{'name': i.name, 'status': i.status, 'ip': i.ip_address} for i in interfaces])

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
