from netmiko import ConnectHandler
import re

def parse_cdp_neighbors(output):
    """
    Parsea la salida de 'show cdp neighbors detail' para extraer IPs y hostnames.
    """
    neighbors = []
    current_neighbor = {}
    
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Device ID:"):
            if current_neighbor:
                neighbors.append(current_neighbor)
            current_neighbor = {'hostname': line.split(":")[-1].strip().split('.')[0]}
        elif line.startswith("IP address:"):
            current_neighbor['ip'] = line.split(":")[-1].strip()
        elif line.startswith("Platform:"):
            # Platform: cisco C7200,  Capabilities: Router
            match = re.search(r'Platform:\s*(.*?),', line)
            if match:
                current_neighbor['hardware'] = match.group(1).strip()
        elif line.startswith("Interface:"):
            # Interface: FastEthernet0/0,  Port ID (outgoing port): FastEthernet0/0
            match = re.search(r'Interface:\s*(.*?),.*Port ID.*?:\s*(.*)', line)
            if match:
                current_neighbor['local_int'] = match.group(1).strip()
                current_neighbor['remote_int'] = match.group(2).strip()
                
    if current_neighbor:
        neighbors.append(current_neighbor)
        
    return neighbors

def discover_network(seed_ip, ssh_creds):
    """
    Descubre la topología usando SSH y CDP saltando desde la IP Semilla.
    """
    topology = {
        'routers': [],
        'links': []
    }
    
    device = {
        'device_type': 'cisco_ios',
        'host': seed_ip,
        'username': ssh_creds['user'],
        'password': ssh_creds['password'],
        'secret': ssh_creds['password']
    }
    
    try:
        net_connect = ConnectHandler(**device)
        net_connect.enable()
        
        # Obtener info local de R1
        hostname_out = net_connect.send_command("show run | inc hostname")
        local_hostname = hostname_out.split()[-1] if hostname_out else "R1"
        
        r1_node = {
            'hostname': local_hostname,
            'ip': seed_ip,
            'hardware': 'Cisco Router'
        }
        topology['routers'].append(r1_node)
        
        # Obtener vecinos CDP
        cdp_out = net_connect.send_command("show cdp neighbors detail")
        neighbors = parse_cdp_neighbors(cdp_out)
        
        for n in neighbors:
            if n.get('ip') and n['ip'] not in [r['ip'] for r in topology['routers']]:
                topology['routers'].append({
                    'hostname': n['hostname'],
                    'ip': n['ip'],
                    'hardware': n.get('hardware', 'Cisco Router')
                })
                
                topology['links'].append({
                    'source': local_hostname,
                    'target': n['hostname'],
                    'source_int': n.get('local_int'),
                    'target_int': n.get('remote_int')
                })
                
        net_connect.disconnect()
        return topology
    except Exception as e:
        print(f"Error en descubrimiento SSH: {e}")
        return None
