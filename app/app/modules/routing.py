"""
Configuración remota de enrutamiento (RIP/OSPF) sobre toda la topología,
incluso cuando los routers no son vecinos directos del seed (R1).

Por qué se necesita esto:
--------------------------------------------------------------------
Antes de activar el protocolo de enrutamiento, la única IP alcanzable
desde la máquina virtual es la del router semilla (R1), porque las
demás redes (los enlaces punto a punto entre routers, y las LANs de
PC1/PC2) no tienen ruta de regreso hacia la subred de gestión.

La versión anterior (configure_routing_all) saltaba SIEMPRE desde R1
hacia cada router de una lista plana ("topology['routers']"), lo cual
solo funciona si ESE router es vecino directo de R1. Con la topología
del examen (R1 - R2 - R3 en cadena, no en estrella), el salto directo
R1 -> R3 fallaría igual que falló R1 -> 8.8.8.2 por SSH desde la MV.

La solución: encadenar los saltos SSH siguiendo la topología real,
descubierta con CDP EN CADA NIVEL (CDP es Capa 2, siempre funciona sin
importar si hay IP routing o no):

    MV --ssh--> R1 --ssh anidado--> R2 --ssh anidado--> R3 --ssh anidado--> ...

En cada nivel se configura el protocolo y luego se usa CDP para saber
a quién saltar después, evitando volver hacia atrás (visited).
"""

import re
import time
import logging
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException

from .discovery import parse_cdp_neighbors, get_hostname

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


ROUTING_COMMANDS = {
    "rip": [
        "router rip",
        "version 2",
        "no auto-summary",
        "network 148.204.0.0",
        "network 8.0.0.0",
    ],
    "ospf": [
        "router ospf 1",
        "network 148.204.0.0 0.0.255.255 area 0",
        "network 8.0.0.0 0.255.255.255 area 0",
    ],
}

# Patrones de prompt/estado que podemos encontrar durante un salto SSH
# anidado dentro de la CLI de otro router.
PROMPT_PATTERNS = {
    "host_key": r"\(yes/no.*?\)",
    "password": r"[Pp]assword:|[Cc]ontrase",
    "priv_exec": r"#\s*$",
    "user_exec": r">\s*$",
    "refused": r"[Rr]efused|timed out|% ?Connection|[Uu]nable to connect",
}


def _expect_multi(net_connect, patterns, timeout=12, poll_interval=0.3):
    """
    Lee el canal en crudo hasta que el buffer acumulado haga match con
    alguno de los patrones dados. Devuelve (nombre_patron, buffer) o
    (None, buffer) si se agota el timeout sin encontrar ninguno.
    """
    buffer = ""
    elapsed = 0.0
    while elapsed < timeout:
        chunk = net_connect.read_channel()
        if chunk:
            buffer += chunk
            for name, pattern in patterns.items():
                if re.search(pattern, buffer):
                    return name, buffer
        time.sleep(poll_interval)
        elapsed += poll_interval
    return None, buffer


def _ssh_hop(net_connect, target_ip, ssh_creds):
    """
    Desde una sesión CLI ya posicionada en modo privilegiado de un
    router, abre un salto SSH ANIDADO hacia target_ip usando el
    cliente SSH nativo del IOS, y deja la sesión en modo privilegiado
    del router destino. Devuelve True/False.
    """
    net_connect.write_channel(f"ssh -l {ssh_creds['user']} {target_ip}\n")
    name, buf = _expect_multi(net_connect, PROMPT_PATTERNS, timeout=10)

    if name == "host_key":
        # Primera conexión: acepta la huella del host (no hay known_hosts)
        net_connect.write_channel("yes\n")
        name, buf = _expect_multi(net_connect, PROMPT_PATTERNS, timeout=10)

    if name in (None, "refused"):
        logger.error(f"No se pudo abrir SSH anidado hacia {target_ip}: ...{buf[-150:]!r}")
        return False

    if name == "password":
        net_connect.write_channel(f"{ssh_creds['password']}\n")
        name, buf = _expect_multi(net_connect, PROMPT_PATTERNS, timeout=10)

    if name == "user_exec":
        net_connect.write_channel("enable\n")
        name, buf = _expect_multi(net_connect, {"password": PROMPT_PATTERNS["password"]}, timeout=6)
        if name == "password":
            net_connect.write_channel(f"{ssh_creds.get('secret', ssh_creds['password'])}\n")
            name, buf = _expect_multi(net_connect, PROMPT_PATTERNS, timeout=10)

    if name != "priv_exec":
        logger.error(f"No se llegó a modo privilegiado en {target_ip}: ...{buf[-150:]!r}")
        return False

    return True


def _push_protocol_config(net_connect, protocol):
    """Aplica los comandos de RIP/OSPF en la sesión (anidada o no) actual."""
    cmds = ROUTING_COMMANDS[protocol]

    logger.info("[DEBUG] Enviando 'conf t'...")
    net_connect.write_channel("conf t\n")
    name, buf = _expect_multi(net_connect, {"config": r"\(config\)#\s*$"}, timeout=6)
    logger.info(f"[DEBUG] Resultado 'conf t': match={name!r} buffer_tail={buf[-80:]!r}")

    for cmd in cmds:
        logger.info(f"[DEBUG] Enviando comando: {cmd!r}")
        net_connect.write_channel(cmd + "\n")
        time.sleep(0.3)

    net_connect.write_channel("end\n")
    name, buf = _expect_multi(net_connect, PROMPT_PATTERNS, timeout=6)
    logger.info(f"[DEBUG] Resultado 'end': match={name!r} buffer_tail={buf[-80:]!r}")

    net_connect.write_channel("write memory\n")
    name, buf = _expect_multi(net_connect, PROMPT_PATTERNS, timeout=12)
    logger.info(f"[DEBUG] Resultado 'write memory': match={name!r} buffer_tail={buf[-80:]!r}")


def _get_cdp_neighbors_current_level(net_connect):
    """Ejecuta 'show cdp neighbors detail' en la sesión actual (top o anidada)."""
    net_connect.write_channel("terminal length 0\n")
    time.sleep(0.3)
    net_connect.read_channel()

    net_connect.write_channel("show cdp neighbors detail\n")
    _, buf = _expect_multi(net_connect, {"priv_exec": r"#\s*$"}, timeout=10)
    return parse_cdp_neighbors(buf)


def _configure_hop(net_connect, current_ip, hostname, protocol, ssh_creds,
                    visited, configured, depth=0, max_depth=10):
    """
    Configura el protocolo en el nivel actual y recursa hacia los
    vecinos CDP no visitados, encadenando saltos SSH anidados.
    """
    visited.add(current_ip)
    _push_protocol_config(net_connect, protocol)
    configured.append(hostname or current_ip)
    logger.info(f"{'  ' * depth}{hostname or current_ip} configurado con {protocol.upper()}.")

    if depth >= max_depth:
        return

    neighbors = _get_cdp_neighbors_current_level(net_connect)

    for n in neighbors:
        n_ip = n.get("ip")
        if not n_ip or n_ip in visited:
            continue  # sin IP gestionable (switch L2) o ya configurado

        if _ssh_hop(net_connect, n_ip, ssh_creds):
            _configure_hop(
                net_connect, n_ip, n.get("hostname"), protocol, ssh_creds,
                visited, configured, depth=depth + 1, max_depth=max_depth,
            )
            # Vuelve al nivel anterior para seguir con el siguiente vecino
            net_connect.write_channel("exit\n")
            _expect_multi(net_connect, PROMPT_PATTERNS, timeout=6)
        else:
            logger.warning(f"Se omite la rama hacia {n.get('hostname')} ({n_ip}).")


def configure_routing_recursive(protocol, seed_ip, ssh_creds, max_depth=10):
    """
    Punto de entrada: activa RIP u OSPF en TODA la red alcanzable desde
    seed_ip, encadenando saltos SSH anidados guiados por CDP en cada
    nivel. No requiere una topología previamente descubierta.

    protocol: 'rip' u 'ospf'
    ssh_creds: {'user':..., 'password':..., 'secret': ... (opcional)}
    """
    if protocol not in ROUTING_COMMANDS:
        return False, f"Protocolo no soportado: {protocol}"

    device = {
        "device_type": "cisco_ios",
        "host": seed_ip,
        "username": ssh_creds["user"],
        "password": ssh_creds["password"],
        "secret": ssh_creds.get("secret", ssh_creds["password"]),
    }

    visited = set()
    configured = []

    try:
        logger.info(f"[DEBUG] Conectando a seed {seed_ip}...")
        net_connect = ConnectHandler(**device)
        logger.info(f"[DEBUG] Conexión establecida a {seed_ip}. Llamando enable()...")
        net_connect.enable()
        logger.info(f"[DEBUG] enable() completado. Prompt actual: {net_connect.find_prompt()!r}")
        net_connect.write_channel("terminal length 0\n")
        time.sleep(0.3)
        net_connect.read_channel()
        logger.info("[DEBUG] terminal length 0 enviado. Obteniendo hostname...")

        seed_hostname = get_hostname(net_connect, fallback=seed_ip)
        logger.info(f"[DEBUG] Hostname obtenido: {seed_hostname}. Iniciando configuración recursiva...")

        _configure_hop(
            net_connect, seed_ip, seed_hostname, protocol, ssh_creds,
            visited, configured, depth=0, max_depth=max_depth,
        )

        net_connect.disconnect()
        return True, f"{protocol.upper()} desplegado en {len(configured)} dispositivo(s): {', '.join(configured)}"

    except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
        return False, f"No se pudo conectar al seed {seed_ip}: {e}"
    except Exception as e:
        logger.exception("Error configurando enrutamiento recursivo")
        return False, str(e)


if __name__ == "__main__":
    creds = {"user": "admin", "password": "cisco123"}
    ok, msg = configure_routing_recursive("ospf", "148.204.56.1", creds)
    print(ok, msg)
