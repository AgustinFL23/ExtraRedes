from .snmp_utils import snmp_get

# OIDs estándar MIB-2 para interfaces
TRAFFIC_OIDS = {
    'ifInOctets': '1.3.6.1.2.1.2.2.1.10',
    'ifOutOctets': '1.3.6.1.2.1.2.2.1.16',
    'ifInUcastPkts': '1.3.6.1.2.1.2.2.1.11',
    'ifOutUcastPkts': '1.3.6.1.2.1.2.2.1.17',
    'ifInNUcastPkts': '1.3.6.1.2.1.2.2.1.12',
    'ifOutNUcastPkts': '1.3.6.1.2.1.2.2.1.18'
}

def get_interface_traffic(ip, if_index, creds):
    """
    Obtiene los contadores crudos de tráfico para una interfaz dada.
    """
    data = {}
    for key, oid_base in TRAFFIC_OIDS.items():
        oid = f"{oid_base}.{if_index}"
        val = snmp_get(ip, oid, creds)
        try:
            data[key] = int(val) if val else 0
        except (ValueError, TypeError):
            data[key] = 0
    return data
