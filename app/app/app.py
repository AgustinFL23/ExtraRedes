import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
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
        from modules.graph_utils import draw_topology
        
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
        
        # Guardar credenciales en sesión para usarlas luego en monitoreo de tráfico dinámico sin hardcodearlas
        session['snmp_creds'] = snmp_creds
        session['ssh_creds'] = ssh_creds
        
        topology = discover_network(seed_ip, ssh_creds)
        
        if topology and len(topology.get('routers', [])) > 0:
            # Dibujar y guardar la topología en static/
            static_dir = os.path.join(app.root_path, 'static')
            draw_topology(topology, static_dir)
            
            # ¡CLAVE PARA CAMBIOS DE TOPOLOGÍA!
            # Limpiamos la BD para que NUNCA sea la fuente de la verdad
            # y refleje siempre la red VIVA recién descubierta.
            Router.query.delete()
            db.session.commit()
            
            for r_node in topology['routers']:
                ip = r_node['ip']
                snmp_info = get_router_info(ip, snmp_creds) or {}
                
                existing = Router.query.filter_by(ip_address=ip).first()
                if not existing:
                    new_r = Router(
                        hostname=snmp_info.get('hostname') or r_node.get('hostname'),
                        ip_address=ip,
                        hardware=snmp_info.get('hardware') or r_node.get('hardware'),
                        os_version=snmp_info.get('os_version', 'Unknown'),
                        uptime=snmp_info.get('uptime', '0'),
                        location=snmp_info.get('location', ''),
                        contact=snmp_info.get('contact', '')
                    )
                    db.session.add(new_r)
                else:
                    if snmp_info:
                        existing.uptime = snmp_info.get('uptime', existing.uptime)
                        existing.os_version = snmp_info.get('os_version', existing.os_version)
            
            db.session.commit()
            return redirect(url_for('view_topology'))
        else:
            return render_template('index.html', alert_error="Fallo en el descubrimiento CDP en la IP Semilla.")
            
    @app.route('/topology')
    def view_topology():
        return render_template('topology.html')

    @app.route('/routing', methods=['POST'])
    def configure_routing_route():
        from modules.routing import configure_routing_recursive

        protocol = request.form.get('protocol')
        seed_ip = request.form.get('seed_ip')
        ssh_creds = {
            'user': request.form.get('ssh_user'),
            'password': request.form.get('ssh_pass')
        }

        # Ya no depende de la BD ni de un discover previo: la topología
        # se recorre en vivo vía CDP en cada salto SSH anidado, por eso
        # esto puede ejecutarse ANTES de "Explorar la red" (como pide
        # el examen: enrutamiento primero, descubrimiento después).
        session['ssh_creds'] = ssh_creds

        success, msg = configure_routing_recursive(protocol, seed_ip, ssh_creds)

        if success:
            return render_template('index.html', alert_success=msg)
        else:
            return render_template('index.html', alert_error=msg)

    @app.route('/routers')
    def view_routers_ui():
        routers = Router.query.all()
        return render_template('routers.html', routers=routers)
        
    @app.route('/routers/<int:router_id>/interfaces')
    def view_interfaces_ui(router_id):
        router = Router.query.get_or_404(router_id)
        interfaces = Interface.query.filter_by(router_id=router_id).all()
        return render_template('interfaces.html', router=router, interfaces=interfaces)

    # --- INCREMENTO 7: Cambio de Datos ---
    @app.route('/router/<int:router_id>/hostname', methods=['POST'])
    def change_hostname(router_id):
        from modules.config_utils import modify_router_netmiko
        router = Router.query.get_or_404(router_id)
        new_hostname = request.form.get('hostname')
        
        creds = session.get('ssh_creds')
        if not creds:
            flash("No hay credenciales SSH en sesión.", "danger")
            return redirect(url_for('view_routers_ui'))
            
        success, msg = modify_router_netmiko(router.ip_address, creds, [f"hostname {new_hostname}"])
        if success:
            router.hostname = new_hostname
            db.session.commit()
            flash(f"Hostname cambiado a {new_hostname}", "success")
        else:
            flash(f"Error cambiando hostname: {msg}", "danger")
        return redirect(url_for('view_routers_ui'))

    @app.route('/router/<int:router_id>/location', methods=['POST'])
    def change_location(router_id):
        from modules.snmp_utils import snmp_set
        router = Router.query.get_or_404(router_id)
        new_location = request.form.get('location')
        
        creds = session.get('snmp_creds')
        if not creds:
            flash("No hay credenciales SNMP en sesión.", "danger")
            return redirect(url_for('view_routers_ui'))
            
        # OID de sysLocation
        success = snmp_set(router.ip_address, '1.3.6.1.2.1.1.6.0', new_location, creds)
        if success:
            router.location = new_location
            db.session.commit()
            flash(f"Locación cambiada a {new_location}", "success")
        else:
            flash("Error cambiando locación vía SNMP", "danger")
        return redirect(url_for('view_routers_ui'))
        
    @app.route('/interface/<int:interface_id>/action', methods=['POST'])
    def change_interface_status(interface_id):
        from modules.config_utils import modify_router_netmiko
        interface = Interface.query.get_or_404(interface_id)
        router = Router.query.get(interface.router_id)
        action = request.form.get('action') # 'shutdown' or 'no_shutdown'
        
        creds = session.get('ssh_creds')
        if not creds:
            flash("No hay credenciales SSH en sesión.", "danger")
            return redirect(url_for('view_interfaces_ui', router_id=router.id))
            
        cmd = "shutdown" if action == "shutdown" else "no shutdown"
        commands = [f"interface {interface.name}", cmd]
        success, msg = modify_router_netmiko(router.ip_address, creds, commands)
        
        if success:
            interface.status = "down" if action == "shutdown" else "up"
            db.session.commit()
            flash(f"Interfaz {interface.name} ahora está {interface.status}", "success")
        else:
            flash(f"Error cambiando estado de interfaz: {msg}", "danger")
            
        return redirect(url_for('view_interfaces_ui', router_id=router.id))

    # --- Endpoints de Monitoreo de Tráfico (Incremento 6) ---
    @app.route('/traffic/<ip>/<if_index>')
    def view_traffic(ip, if_index):
        return render_template('traffic.html', ip=ip, if_index=if_index)

    @app.route('/api/traffic/<ip>/<if_index>')
    def api_traffic(ip, if_index):
        from modules.traffic_utils import get_interface_traffic
        
        # Recuperar credenciales de la sesión (sin hardcodearlas)
        creds = session.get('snmp_creds')
        if not creds:
            return jsonify({"error": "No hay credenciales SNMP en sesión. Realice el descubrimiento primero."}), 403
            
        data = get_interface_traffic(ip, if_index, creds)
        return jsonify(data)

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
