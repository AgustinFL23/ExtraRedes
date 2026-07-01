"""
Incremento 8 — Sistema de Alertas
Componentes:
  1. monitor_loop()    - Hilo que corre cada 30s: revisa estado de interfaces via SNMP
                         y perdida de paquetes via Netmiko ping.
  2. start_trap_receiver() - Hilo que escucha en UDP 1620 los Traps SNMP enviados por
                             los routers (linkDown, linkUp, coldStart, etc.)
  3. create_alert()    - Función de apoyo para insertar alertas en la BD de forma
                         thread-safe usando el contexto de la app Flask.
"""

import threading
import time
import socket
import struct
from datetime import datetime

# ────────────────────────────────────────────────────────────────────────────────
# OIDs de estado de interfaz (ifOperStatus): 1 = up, 2 = down
# ────────────────────────────────────────────────────────────────────────────────
OID_IF_OPER_STATUS = '1.3.6.1.2.1.2.2.1.8'


def create_alert(app, event_type, description, severity='warning'):
    """Inserta una alerta en la BD usando el contexto de la app Flask."""
    with app.app_context():
        from models import db, Alert
        alert = Alert(
            event_type=event_type,
            description=description,
            severity=severity,
            timestamp=datetime.utcnow()
        )
        db.session.add(alert)
        db.session.commit()
        print(f"[ALERTA] [{severity.upper()}] {event_type}: {description}")


def check_ping_loss(ip, ssh_creds):
    """
    Conecta por SSH al router y ejecuta 'ping <ip> repeat 10'.
    Devuelve el porcentaje de pérdida de paquetes (0-100).
    Retorna None si falla la conexión SSH.
    """
    from netmiko import ConnectHandler
    import re
    try:
        device = {
            'device_type': 'cisco_ios',
            'host': ip,
            'username': ssh_creds['user'],
            'password': ssh_creds['password'],
            'secret': ssh_creds['password'],
            'timeout': 10
        }
        conn = ConnectHandler(**device)
        # Ping al loopback o a sí mismo — confirma que el dispositivo responde
        output = conn.send_command(f"ping {ip} repeat 10", read_timeout=15)
        conn.disconnect()
        
        # Parsear "Success rate is X percent"
        match = re.search(r'Success rate is (\d+) percent', output)
        if match:
            success_rate = int(match.group(1))
            loss = 100 - success_rate
            return loss
        return None
    except Exception as e:
        print(f"[Monitor] No se pudo hacer ping a {ip}: {e}")
        return None


def check_interface_status_snmp(ip, if_index, creds):
    """
    Consulta ifOperStatus para una interfaz via SNMP.
    Devuelve 'up', 'down', o None si falla.
    """
    from modules.snmp_utils import snmp_get
    oid = f"{OID_IF_OPER_STATUS}.{if_index}"
    val = snmp_get(ip, oid, creds)
    if val is None:
        return None
    return 'up' if str(val) == '1' else 'down'


def monitor_loop(app):
    """
    Bucle principal del monitor. Se ejecuta en un hilo de fondo.
    Cada 30 segundos revisa todos los routers y sus interfaces.
    Genera alertas si:
      - Una interfaz cambia de estado (up → down o down → up)
      - La pérdida de paquetes supera el 25%
    """
    # Estado previo de interfaces para detectar cambios
    prev_status = {}  # { "ip:if_index": "up|down" }
    
    # Esperar 10 segundos antes del primer ciclo para dejar iniciar la app
    time.sleep(10)
    
    while True:
        try:
            with app.app_context():
                from models import Router, Interface
                from flask import current_app
                
                creds = app.config.get('SNMP_CREDS')
                ssh_creds = app.config.get('SSH_CREDS')
                
                if not creds or not ssh_creds:
                    # Aún no se ha ejecutado el descubrimiento, esperar
                    time.sleep(30)
                    continue
                
                routers = Router.query.all()
                
                for router in routers:
                    ip = router.ip_address
                    
                    # — Verificar pérdida de paquetes —
                    loss = check_ping_loss(ip, ssh_creds)
                    if loss is not None and loss > 25:
                        create_alert(
                            app,
                            event_type='PING_LOSS',
                            description=f"Router {router.hostname} ({ip}): pérdida de paquetes = {loss}%",
                            severity='critical' if loss > 50 else 'warning'
                        )
                    
                    # — Verificar estado de interfaces via SNMP —
                    interfaces = Interface.query.filter_by(router_id=router.id).all()
                    for intf in interfaces:
                        if_index = intf.mask  # mask guarda el índice SNMP
                        if not if_index:
                            continue
                        
                        key = f"{ip}:{if_index}"
                        current = check_interface_status_snmp(ip, if_index, creds)
                        
                        if current is None:
                            continue
                        
                        prev = prev_status.get(key)
                        
                        if prev and prev != current:
                            # ¡Cambio de estado detectado!
                            severity = 'critical' if current == 'down' else 'info'
                            create_alert(
                                app,
                                event_type='INTERFACE_CHANGE',
                                description=(
                                    f"{router.hostname} ({ip}) — "
                                    f"{intf.name}: cambió de {prev.upper()} a {current.upper()}"
                                ),
                                severity=severity
                            )
                            # Actualizar estado en BD
                            intf.status = current
                            from models import db
                            db.session.commit()
                        
                        prev_status[key] = current

        except Exception as e:
            print(f"[Monitor] Error en ciclo de monitoreo: {e}")
        
        time.sleep(30)


# ────────────────────────────────────────────────────────────────────────────────
# Receptor de Traps SNMP (UDP 1620 — no requiere root como el 162)
# ────────────────────────────────────────────────────────────────────────────────

# Mapa básico de tipo de trap por comunidad o contenido del paquete
TRAP_TYPE_NAMES = {
    0: 'coldStart',
    1: 'warmStart',
    2: 'linkDown',
    3: 'linkUp',
    4: 'authenticationFailure',
    5: 'egpNeighborLoss',
    6: 'enterpriseSpecific'
}

TRAP_SEVERITY = {
    'coldStart': 'info',
    'warmStart': 'info',
    'linkDown': 'critical',
    'linkUp': 'info',
    'authenticationFailure': 'warning',
    'egpNeighborLoss': 'warning',
    'enterpriseSpecific': 'info'
}


def parse_snmp_v2_trap(data, src_ip):
    """
    Parsea mínimamente un paquete SNMPv2c para extraer el OID del trap.
    Para Cisco IOS, los traps vienen como PDUs con OID en sysUpTime + snmpTrapOID.
    Retorna (event_type, description, severity).
    """
    # Lectura muy básica — extraemos el texto del paquete como heurística
    # OIDs conocidos de linkDown y linkUp
    pkt_hex = data.hex()
    
    if '1.3.6.1.6.3.1.1.5.3' in str(data) or b'\x03' in data[20:25]:
        event = 'linkDown'
    elif '1.3.6.1.6.3.1.1.5.4' in str(data) or b'\x04' in data[20:25]:
        event = 'linkUp'
    elif b'coldStart' in data or b'\x01' in data[10:15]:
        event = 'coldStart'
    else:
        event = 'SNMP_TRAP'
    
    severity = TRAP_SEVERITY.get(event, 'info')
    description = f"Trap recibido desde {src_ip}: evento {event}"
    return event, description, severity


def start_trap_receiver(app, port=1620):
    """
    Lanza un hilo que escucha traps SNMP en el puerto UDP especificado.
    Los routers deben estar configurados con:
      snmp-server host <IP_MV> version 2c redes2026
    y redirigir al puerto 1620 (o configurar con iptables: 162 → 1620).
    """
    def _listen():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', port))
            sock.settimeout(2.0)  # Timeout para poder salir limpiamente
            print(f"[Trap Receiver] Escuchando traps en UDP :{port}")
            
            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    src_ip = addr[0]
                    event, description, severity = parse_snmp_v2_trap(data, src_ip)
                    create_alert(app, event_type=event, description=description, severity=severity)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[Trap Receiver] Error procesando trap: {e}")
        except Exception as e:
            print(f"[Trap Receiver] No se pudo iniciar en puerto {port}: {e}")
    
    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    return t


def start_monitor(app):
    """
    Inicia ambos hilos de fondo (monitor de polling y receptor de traps).
    Se llama una sola vez desde create_app().
    """
    t_monitor = threading.Thread(target=monitor_loop, args=(app,), daemon=True)
    t_monitor.start()
    
    start_trap_receiver(app, port=1620)
    
    print("[Monitor] Sistema de alertas iniciado.")
