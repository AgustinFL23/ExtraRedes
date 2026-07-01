from netmiko import ConnectHandler
import re

def parse_cdp_neighbors(output):
    """
    Parsea la salida de 'show cdp neighbors detail' para extraer
    IPs, hostnames, plataforma e interfaces de cada vecino.
    """
    neighbors = []
    current_neighbor = {}
    
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Device ID:"):
            if current_neighbor:
                neighbors.append(current_neighbor)
            current_neighbor = {'hostname': line.split(":")[-1].strip().split('.')[0]}
        elif line.startswith("IP address:") and 'ip' not in current_neighbor:
            current_neighbor['ip'] = line.split(":")[-1].strip()
        elif line.startswith("Platform:"):
            match = re.search(r'Platform:\s*(.*?),\s*Capabilities:\s*(.*)', line)
            if match:
                current_neighbor['hardware'] = match.group(1).strip()
                # Si dice "Trans-Bridge" o "Host" es un terminal, no un router
                caps = match.group(2).lower()
                current_neighbor['is_host'] = 'router' not in caps
        elif line.startswith("Interface:"):
            match = re.search(r'Interface:\s*(.*?),.*Port ID.*?:\s*(.*)', line)
            if match:
                current_neighbor['local_int'] = match.group(1).strip()
                current_neighbor['remote_int'] = match.group(2).strip()
                
    if current_neighbor:
        neighbors.append(current_neighbor)
        
    return neighbors


def parse_interfaces(output):
    """
    Parsea 'show ip interface brief' para obtener la lista de interfaces,
    sus IPs y su estado (up/down).
    """
    interfaces = []
    for line in output.splitlines():
        # Omitir la cabecera de la tabla
        if line.startswith("Interface") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 6:
            name = parts[0]
            ip = parts[1] if parts[1] != "unassigned" else None
            status = "up" if parts[4].lower() == "up" else "down"
            interfaces.append({
                'name': name,
                'ip_address': ip,
                'status': status
            })
    return interfaces


def discover_network(seed_ip, ssh_creds):
    """
    Descubre la topología usando SSH y CDP saltando desde la IP Semilla.
    Además obtiene las interfaces del router semilla.
    Deduplica enlaces (A→B y B→A se tratan como uno solo).
    """
    topology = {
        'routers': [],
        'links': [],
        'hosts': []
    }
    
    device = {
        'device_type': 'cisco_ios',
        'host': seed_ip,
        'username': ssh_creds['user'],
        'password': ssh_creds['password'],
        'secret': ssh_creds['password']
    }
    
    # Set para evitar duplicar enlaces (par ordenado)
    seen_links = set()
    
    try:
        net_connect = ConnectHandler(**device)
        net_connect.enable()
        
        # Obtener info local del router semilla
        hostname_out = net_connect.send_command("show run | inc hostname")
        local_hostname = hostname_out.split()[-1] if hostname_out else "R1"
        
        # Obtener interfaces del router semilla
        intf_out = net_connect.send_command("show ip interface brief")
        seed_interfaces = parse_interfaces(intf_out)
        
        r1_node = {
            'hostname': local_hostname,
            'ip': seed_ip,
            'hardware': 'Cisco Router',
            'interfaces': seed_interfaces
        }
        topology['routers'].append(r1_node)
        
        # Obtener vecinos CDP
        cdp_out = net_connect.send_command("show cdp neighbors detail")
        neighbors = parse_cdp_neighbors(cdp_out)
        net_connect.disconnect()
        
        for n in neighbors:
            if not n.get('ip'):
                continue
            
            if n.get('is_host'):
                # Es una terminal (PC, switch, etc.)
                if n['ip'] not in [h['ip'] for h in topology['hosts']]:
                    topology['hosts'].append({
                        'hostname': n['hostname'],
                        'ip': n['ip'],
                        'hardware': n.get('hardware', 'Host')
                    })
            else:
                # Es un router
                if n['ip'] not in [r['ip'] for r in topology['routers']]:
                    # Intentar conectarse al vecino para obtener sus interfaces
                    neighbor_interfaces = []
                    try:
                        nbr_device = {**device, 'host': n['ip']}
                        nbr_conn = ConnectHandler(**nbr_device)
                        nbr_conn.enable()
                        nbr_intf_out = nbr_conn.send_command("show ip interface brief")
                        neighbor_interfaces = parse_interfaces(nbr_intf_out)
                        nbr_conn.disconnect()
                    except Exception as e:
                        print(f"No se pudo obtener interfaces de {n['hostname']} ({n['ip']}): {e}")
                    
                    topology['routers'].append({
                        'hostname': n['hostname'],
                        'ip': n['ip'],
                        'hardware': n.get('hardware', 'Cisco Router'),
                        'interfaces': neighbor_interfaces
                    })
                
                # Deduplicar enlace: guardamos el par en orden alfabético
                link_key = tuple(sorted([local_hostname, n['hostname']]))
                if link_key not in seen_links:
                    seen_links.add(link_key)
                    topology['links'].append({
                        'source': local_hostname,
                        'target': n['hostname'],
                        'source_int': n.get('local_int', ''),
                        'target_int': n.get('remote_int', '')
                    })
                    
        return topology
    except Exception as e:
        print(f"Error en descubrimiento SSH: {e}")
        return None
