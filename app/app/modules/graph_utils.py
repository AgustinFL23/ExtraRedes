import pygraphviz as pgv
import os

def draw_topology(topology, static_dir):
    """
    Genera un archivo topology.png en la carpeta static usando pygraphviz.
    topology: dict con 'routers' (lista de nodos) y 'links' (lista de aristas)
    """
    if not topology or not topology.get('routers'):
        return False
        
    try:
        # Asegurar que el directorio static exista
        os.makedirs(static_dir, exist_ok=True)
        
        A = pgv.AGraph(strict=False, directed=False)
        A.graph_attr['rankdir'] = 'LR'
        A.graph_attr['bgcolor'] = 'transparent'
        
        A.node_attr['shape'] = 'box'
        A.node_attr['style'] = 'filled,rounded'
        A.node_attr['fillcolor'] = '#0d6efd'
        A.node_attr['fontcolor'] = 'white'
        A.node_attr['fontname'] = 'Helvetica'
        A.node_attr['penwidth'] = 0
        
        for r in topology['routers']:
            A.add_node(r['hostname'], label=f"{r['hostname']}\n{r.get('ip', '')}")
            
        for link in topology.get('links', []):
            label = f"{link.get('source_int', '')} - {link.get('target_int', '')}"
            A.add_edge(link['source'], link['target'], color='#888888', penwidth=2, label=label, fontcolor='#aaaaaa', fontsize=10)
            
        out_path = os.path.join(static_dir, 'topology.png')
        A.layout(prog='dot')
        A.draw(out_path)
        return True
    except Exception as e:
        print(f"Error generando topología: {e}")
        return False
