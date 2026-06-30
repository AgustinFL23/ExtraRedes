from netmiko import ConnectHandler
import time

def configure_router(net_connect, protocol):
    commands = []
    if protocol == 'rip':
        commands = [
            'router rip',
            'version 2',
            'no auto-summary',
            'network 148.204.0.0',
            'network 8.0.0.0'
        ]
    elif protocol == 'ospf':
        commands = [
            'router ospf 1',
            'network 148.204.0.0 0.0.255.255 area 0',
            'network 8.0.0.0 0.255.255.255 area 0'
        ]
    net_connect.send_config_set(commands)
    net_connect.save_config()

def configure_routing_all(protocol, seed_ip, ssh_creds, topology):
    """
    Despliega RIP/OSPF en toda la red. Como la MV no puede alcanzar a R2/R3
    porque no hay rutas de regreso, usamos R1 como salto (Jump Host) vía SSH.
    """
    device_r1 = {
        'device_type': 'cisco_ios',
        'host': seed_ip,
        'username': ssh_creds['user'],
        'password': ssh_creds['password'],
        'secret': ssh_creds['password']
    }
    
    try:
        # 1. Conectar a R1 y configurar
        net_connect = ConnectHandler(**device_r1)
        net_connect.enable()
        configure_router(net_connect, protocol)
        print("R1 configurado.")
        
        # 2. Iterar sobre los otros routers en la topología descubierta
        for router in topology['routers']:
            if router['ip'] != seed_ip:
                target_ip = router['ip']
                print(f"Saltando hacia {router['hostname']} ({target_ip}) desde R1...")
                
                # Iniciar sesión SSH desde R1 hacia R2/R3
                net_connect.write_channel(f"ssh -l {ssh_creds['user']} {target_ip}\n")
                time.sleep(2)
                output = net_connect.read_channel()
                
                if "assword" in output or "Contraseña" in output:
                    net_connect.write_channel(f"{ssh_creds['password']}\n")
                    time.sleep(2)
                
                # Ahora estamos en la CLI de R2/R3, solicitamos enable
                net_connect.write_channel("enable\n")
                time.sleep(1)
                net_connect.write_channel(f"{ssh_creds['password']}\n")
                time.sleep(1)
                
                # Enviar configuración manualmente ya que estamos en una sesión anidada
                net_connect.write_channel("conf t\n")
                time.sleep(1)
                
                if protocol == 'rip':
                    net_connect.write_channel("router rip\nversion 2\nno auto-summary\nnetwork 148.204.0.0\nnetwork 8.0.0.0\n")
                elif protocol == 'ospf':
                    net_connect.write_channel("router ospf 1\nnetwork 148.204.0.0 0.0.255.255 area 0\nnetwork 8.0.0.0 0.255.255.255 area 0\n")
                
                time.sleep(2)
                net_connect.write_channel("end\nwrite\n")
                time.sleep(2)
                
                # Salir de R2/R3 para volver a R1
                net_connect.write_channel("exit\n")
                time.sleep(1)
                print(f"{router['hostname']} configurado.")
                
        net_connect.disconnect()
        return True, "Enrutamiento desplegado en TODA la topología."
    except Exception as e:
        return False, str(e)
