from netmiko import ConnectHandler
import time

def modify_router_netmiko(target_ip, ssh_creds, commands, seed_ip=None):
    """
    Aplica comandos de configuración en target_ip.
    Si seed_ip se pasa y es distinto de target_ip, usa seed_ip como salto (Jump Host).
    """
    device = {
        'device_type': 'cisco_ios',
        'host': seed_ip if seed_ip else target_ip,
        'username': ssh_creds['user'],
        'password': ssh_creds['password'],
        'secret': ssh_creds['password']
    }
    
    try:
        net_connect = ConnectHandler(**device)
        net_connect.enable()
        
        if seed_ip and seed_ip != target_ip:
            # Lógica de salto (Jump Host)
            net_connect.write_channel(f"ssh -l {ssh_creds['user']} {target_ip}\n")
            time.sleep(2)
            output = net_connect.read_channel()
            if "assword" in output or "Contraseña" in output:
                net_connect.write_channel(f"{ssh_creds['password']}\n")
                time.sleep(2)
                
            net_connect.write_channel("enable\n")
            time.sleep(1)
            net_connect.write_channel(f"{ssh_creds['password']}\n")
            time.sleep(1)
            
            net_connect.write_channel("conf t\n")
            time.sleep(1)
            for cmd in commands:
                net_connect.write_channel(f"{cmd}\n")
                time.sleep(1)
            net_connect.write_channel("end\nwrite\n")
            time.sleep(2)
            net_connect.write_channel("exit\n")
            time.sleep(1)
        else:
            # Conexión directa
            net_connect.send_config_set(commands)
            net_connect.save_config()
            
        net_connect.disconnect()
        return True, "Configuración aplicada exitosamente."
    except Exception as e:
        return False, f"Error SSH: {str(e)}"
