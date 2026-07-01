import pygraphviz as pgv
import os

# Ícono de router como texto ASCII art embebido en el nodo
ROUTER_ICON = "⬡"  # Se puede cambiar por texto simple

def draw_topology(topology, static_dir):
    """
    Genera topology.png con:
    - Nodos de router: ícono + hostname (sin IP para limpieza visual)
    - Nodos de host: forma distinta (PC)
    - Aristas con labels de interfaz (fuente → destino)
    - Sin enlaces duplicados (ya se garantiza en discovery.py)
    """
    if not topology or not topology.get('routers'):
        return False
        
    try:
        os.makedirs(static_dir, exist_ok=True)
        
        # Grafo no dirigido para evitar flechas duplicadas
        A = pgv.AGraph(strict=False, directed=False)
        A.graph_attr['rankdir'] = 'LR'
        A.graph_attr['bgcolor'] = '#1a1a2e'
        A.graph_attr['pad'] = '0.5'
        A.graph_attr['splines'] = 'ortho'
        
        # Atributos globales para aristas
        A.edge_attr['color'] = '#4ecca3'
        A.edge_attr['penwidth'] = '2'
        A.edge_attr['fontcolor'] = '#cccccc'
        A.edge_attr['fontsize'] = '9'
        A.edge_attr['fontname'] = 'Helvetica'
        
        # Agregar nodos de routers
        for r in topology['routers']:
            hostname = r['hostname']
            A.add_node(
                hostname,
                label=f"[ {hostname} ]",
                shape='box',
                style='filled,rounded',
                fillcolor='#16213e',
                color='#4ecca3',
                fontcolor='#e0e0e0',
                fontname='Helvetica Bold',
                fontsize='11',
                penwidth='2',
                width='1.5',
                height='0.6'
            )
        
        # Agregar nodos de hosts/terminales con forma diferente
        for h in topology.get('hosts', []):
            A.add_node(
                h['hostname'],
                label=f"PC: {h['hostname']}",
                shape='component',
                style='filled',
                fillcolor='#0f3460',
                color='#e94560',
                fontcolor='#ffffff',
                fontname='Helvetica',
                fontsize='10',
                penwidth='1.5'
            )
        
        # Agregar enlaces con labels de interfaz
        for link in topology.get('links', []):
            src_int = link.get('source_int', '')
            tgt_int = link.get('target_int', '')
            # Label: "Fa0/0 ↔ Fa0/1"
            edge_label = f"{src_int} ↔ {tgt_int}" if src_int and tgt_int else ""
            A.add_edge(
                link['source'],
                link['target'],
                label=edge_label
            )
            
        out_path = os.path.join(static_dir, 'topology.png')
        A.layout(prog='dot')
        A.draw(out_path, format='png')
        return True
    except Exception as e:
        print(f"Error generando topología: {e}")
        return False
