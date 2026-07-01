import re
import logging
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def clean_hostname(raw):
    """Limpia 'Device ID' de dominios y de seriales entre paréntesis."""
    name = raw.strip()
    # Quita serial tipo R2(FTX1840ALGH)
    name = re.sub(r"\(.*?\)", "", name).strip()
    # Quita dominio tipo R2.midominio.com -> R2
    name = name.split(".")[0]
    return name


def parse_cdp_neighbors(output):
    """
    Parsea la salida de 'show cdp neighbors detail' para extraer IPs,
    hostnames, plataforma e interfaces. Soporta múltiples direcciones IP
    por vecino y variantes de formato (IP address / IPv4 Address).
    """
    neighbors = []
    current = {}

    def flush():
        if current:
            # Si no hay IP, igual se conserva el vecino (sin 'ip')
            neighbors.append(current.copy())

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if line.startswith("Device ID:"):
            flush()
            current.clear()
            current["hostname"] = clean_hostname(line.split(":", 1)[-1])
            current["ips"] = []

        elif line.startswith("IP address:") or line.startswith("IPv4 Address:"):
            ip = line.split(":", 1)[-1].strip()
            if ip:
                current.setdefault("ips", []).append(ip)

        elif line.startswith("Platform:"):
            # Platform: cisco C7200,  Capabilities: Router
            match = re.search(r"Platform:\s*(.*?),", line)
            if match:
                current["hardware"] = match.group(1).strip()

        elif line.startswith("Interface:"):
            # Interface: FastEthernet0/0,  Port ID (outgoing port): FastEthernet0/0
            match = re.search(r"Interface:\s*(.*?),.*Port ID.*?:\s*(.*)", line)
            if match:
                current["local_int"] = match.group(1).strip()
                current["remote_int"] = match.group(2).strip()

    flush()

    # Normaliza: usa la primera IP como 'ip' principal si existe
    for n in neighbors:
        ips = n.get("ips", [])
        n["ip"] = ips[0] if ips else None

    return neighbors


def get_hostname(net_connect, fallback):
    """Obtiene el hostname real del dispositivo de forma robusta."""
    try:
        out = net_connect.send_command("show run | inc ^hostname")
        if out and "hostname" in out.lower():
            return out.split()[-1].strip()
    except Exception as e:
        logger.warning(f"No se pudo obtener hostname, usando fallback: {e}")
    return fallback


def discover_network(seed_ip, ssh_creds, max_hops=None):
    """
    Descubre la topología completa (BFS) usando SSH + CDP a partir de
    una IP semilla. Todos los dispositivos se asumen Cisco IOS.

    ssh_creds: {'user': ..., 'password': ..., 'secret': ... (opcional)}
    max_hops: número máximo de saltos desde la semilla (None = sin límite)
    """
    topology = {"routers": {}, "links": []}   # routers indexado por IP para evitar duplicados
    visited = set()
    # cola de (ip, hops_desde_semilla)
    to_visit = [(seed_ip, 0)]
    queued = {seed_ip}

    while to_visit:
        ip, hops = to_visit.pop(0)

        if ip in visited:
            continue
        visited.add(ip)

        if max_hops is not None and hops > max_hops:
            continue

        device = {
            "device_type": "cisco_ios",
            "host": ip,
            "username": ssh_creds["user"],
            "password": ssh_creds["password"],
            "secret": ssh_creds.get("secret", ssh_creds["password"]),
        }

        try:
            net_connect = ConnectHandler(**device)
            net_connect.enable()

            local_hostname = get_hostname(net_connect, fallback=ip)

            # Registra el nodo local si no existe aún
            topology["routers"].setdefault(ip, {
                "hostname": local_hostname,
                "ip": ip,
                "hardware": "Cisco",
            })

            cdp_out = net_connect.send_command("show cdp neighbors detail")
            neighbors = parse_cdp_neighbors(cdp_out)
            net_connect.disconnect()

        except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
            logger.error(f"No se pudo conectar a {ip}: {e}")
            continue
        except Exception as e:
            logger.error(f"Error inesperado en {ip}: {e}")
            continue

        for n in neighbors:
            neighbor_ip = n.get("ip")

            if not neighbor_ip:
                # Vecino sin IP gestionable: se loguea pero no se puede
                # ni registrar como nodo consultable ni encolar.
                logger.warning(
                    f"Vecino '{n.get('hostname')}' de {local_hostname} "
                    f"sin IP CDP; se omite de la cola pero no del link si aplica."
                )

            # Registra el router vecino si aún no existe en la topología
            if neighbor_ip and neighbor_ip not in topology["routers"]:
                topology["routers"][neighbor_ip] = {
                    "hostname": n["hostname"],
                    "ip": neighbor_ip,
                    "hardware": n.get("hardware", "Cisco"),
                }

            # El link se registra SIEMPRE que haya info de vecino,
            # exista o no ya el router (soporta anillos/mallas)
            topology["links"].append({
                "source": local_hostname,
                "source_ip": ip,
                "target": n.get("hostname"),
                "target_ip": neighbor_ip,
                "source_int": n.get("local_int"),
                "target_int": n.get("remote_int"),
            })

            # Encola el vecino para seguir descubriendo (BFS)
            if neighbor_ip and neighbor_ip not in visited and neighbor_ip not in queued:
                to_visit.append((neighbor_ip, hops + 1))
                queued.add(neighbor_ip)

    # Deduplicar links (A->B y B->A cuentan como el mismo enlace físico)
    topology["links"] = dedup_links(topology["links"])
    topology["routers"] = list(topology["routers"].values())

    return topology


def dedup_links(links):
    """Elimina enlaces duplicados considerando A-B == B-A."""
    seen = set()
    result = []
    for link in links:
        key = tuple(sorted([
            (link.get("source_ip"), link.get("source_int")),
            (link.get("target_ip"), link.get("target_int")),
        ]))
        if key not in seen:
            seen.add(key)
            result.append(link)
    return result


if __name__ == "__main__":
    creds = {"user": "admin", "password": "cisco123"}
    topo = discover_network("192.168.1.1", creds, max_hops=5)
    if topo:
        print(f"\nRouters/Switches descubiertos: {len(topo['routers'])}")
        for r in topo["routers"]:
            print(f"  - {r['hostname']} ({r['ip']}) [{r['hardware']}]")

        print(f"\nEnlaces descubiertos: {len(topo['links'])}")
        for l in topo["links"]:
            print(
                f"  - {l['source']} ({l['source_int']}) "
                f"<-> {l['target']} ({l['target_int']})"
            )
