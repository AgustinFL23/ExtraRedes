from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UsmUserData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, OctetString,
    getCmd, setCmd,
    usmHMACSHAAuthProtocol, usmDESPrivProtocol
)

def get_auth_data(creds):
    if creds.get('snmp_version') == '2c':
        return CommunityData(creds.get('snmp_community', 'public'), mpModel=1)
    else:
        return UsmUserData(
            creds.get('v3_user'),
            authKey=creds.get('v3_auth'),
            privKey=creds.get('v3_priv'),
            authProtocol=usmHMACSHAAuthProtocol,
            privProtocol=usmDESPrivProtocol
        )

def snmp_get(ip, oid, creds):
    auth_data = get_auth_data(creds)
    iterator = getCmd(
        SnmpEngine(),
        auth_data,
        UdpTransportTarget((ip, 161), timeout=2, retries=1),
        ContextData(),
        ObjectType(ObjectIdentity(oid))
    )
    
    errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
    if errorIndication or errorStatus:
        print(f"SNMP Error on {ip}: {errorIndication or errorStatus}")
        return None
    for varBind in varBinds:
        return str(varBind[1])
    return None

def snmp_set(ip, oid, value, creds):
    auth_data = get_auth_data(creds)
    iterator = setCmd(
        SnmpEngine(),
        auth_data,
        UdpTransportTarget((ip, 161), timeout=2, retries=1),
        ContextData(),
        ObjectType(ObjectIdentity(oid), OctetString(value))
    )
    
    errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
    if errorIndication or errorStatus:
        print(f"SNMP Set Error on {ip}: {errorIndication or errorStatus}")
        return False
    return True

def get_router_info(ip, creds):
    info = {}
    info['hostname'] = snmp_get(ip, '1.3.6.1.2.1.1.5.0', creds)
    info['contact'] = snmp_get(ip, '1.3.6.1.2.1.1.4.0', creds)
    info['location'] = snmp_get(ip, '1.3.6.1.2.1.1.6.0', creds)
    
    uptime = snmp_get(ip, '1.3.6.1.2.1.1.3.0', creds)
    info['uptime'] = uptime if uptime else "0"
    
    descr = snmp_get(ip, '1.3.6.1.2.1.1.1.0', creds)
    if descr:
        info['hardware'] = "Cisco Router (Simulated)"
        # Simple parse for OS version from standard Cisco sysDescr
        import re
        match = re.search(r'Version ([^, ]+)', descr)
        info['os_version'] = match.group(1) if match else "Unknown"
    else:
        info['hardware'] = "Unknown"
        info['os_version'] = "Unknown"
        
    return info
