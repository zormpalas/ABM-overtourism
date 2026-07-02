import geopandas as gpd
import numpy as np
import networkx as nx
import matplotlib.pyplot as plt
from shapely.geometry import LineString, Point

DATA_PATH = "data/greece-251102-free.shp"

def load_and_visualize_graph():
    """
    Load the city data, build the graph, and visualize it.
    """
    # LOAD CITY
    places = gpd.read_file(DATA_PATH, layer="gis_osm_places_free_1")
    city_name = "Πάτρα"
    city = places[places["name"] == city_name]

    if city.empty:
        raise ValueError("City not found.")

    city_2100 = city.to_crs(2100)

    buffer_m = 2_000
    buffer_2100 = city_2100.buffer(buffer_m)
    buffer_4326 = buffer_2100.to_crs(4326)

    minx, miny, maxx, maxy = buffer_4326.total_bounds

    # LOAD ROADS WITH BBOX FILTER
    roads = gpd.read_file(DATA_PATH, layer="gis_osm_roads_free_1", bbox=(minx, miny, maxx, maxy))
    roads_2100 = roads.to_crs(2100)
    
    # FILTER TO PEDESTRIAN-ACCESSIBLE ROADS
    pedestrian_types = ['footway', 'path', 'pedestrian', 'steps', 'residential', 
                       'living_street', 'service', 'unclassified', 'tertiary', 
                       'secondary', 'primary']
    
    if 'fclass' in roads_2100.columns:
        roads_2100 = roads_2100[roads_2100['fclass'].isin(pedestrian_types)]
    
    print(f"Total roads after filtering: {len(roads_2100)}")

    # BUILD ROAD NETWORK GRAPH
    print("Building road network graph...")
    G = nx.Graph()
    edge_geometries = {}
    
    snap_tolerance = 5.0
    coord_to_node = {}
    node_counter = 0
    
    def get_or_create_node(coord):
        nonlocal node_counter
        rounded = (round(coord[0] / snap_tolerance) * snap_tolerance,
                  round(coord[1] / snap_tolerance) * snap_tolerance)
        
        if rounded not in coord_to_node:
            coord_to_node[rounded] = node_counter
            G.add_node(node_counter, pos=(coord[0], coord[1]))
            node_counter += 1
        
        return coord_to_node[rounded]
    
    # COLLECT ALL LINE GEOMETRIES FIRST
    all_lines = []
    
    for idx, road in roads_2100.iterrows():
        geom = road.geometry

        if geom.geom_type == 'LineString':
            lines = [geom]
        elif geom.geom_type == 'MultiLineString':
            lines = list(geom.geoms)
        else:
            continue

        for line in lines:
            all_lines.append({
                'geometry': line,
                'road_type': road.get('fclass', 'unknown')
            })
    
    print(f"Collected {len(all_lines)} line segments")

    # FIND ALL INTERSECTION POINTS BETWEEN LINES   
    print("Finding line-to-line intersections...")
    intersection_points = set()
    
    for i, line1_info in enumerate(all_lines):
        line1 = line1_info['geometry']
        
        # Add endpoints
        coords1 = list(line1.coords)
        intersection_points.add(coords1[0])
        intersection_points.add(coords1[-1])
        
        # Check intersection with all other lines
        for j, line2_info in enumerate(all_lines):
            if i >= j:  # Avoid duplicate checks
                continue
            
            line2 = line2_info['geometry']
            
            # Check if lines intersect
            if line1.intersects(line2):
                intersection = line1.intersection(line2)
                
                # Handle different intersection types
                if intersection.geom_type == 'Point':
                    # Single point intersection
                    pt = intersection
                    # Don't add if it's at the endpoints (already added)
                    coords1 = list(line1.coords)
                    coords2 = list(line2.coords)
                    
                    is_endpoint = False
                    for endpoint in [coords1[0], coords1[-1], coords2[0], coords2[-1]]:
                        if Point(endpoint).distance(pt) < snap_tolerance:
                            is_endpoint = True
                            break
                    
                    if not is_endpoint:
                        intersection_points.add((pt.x, pt.y))
                
                elif intersection.geom_type == 'MultiPoint':
                    # Multiple intersection points
                    for pt in intersection.geoms:
                        intersection_points.add((pt.x, pt.y))
                
                elif intersection.geom_type == 'LineString':
                    # Lines overlap - add both ends of overlap
                    overlap_coords = list(intersection.coords)
                    intersection_points.add(overlap_coords[0])
                    intersection_points.add(overlap_coords[-1])
    
    print(f"Found {len(intersection_points)} intersection points")
    
    # CREATE NODES FOR ALL INTERSECTION POINTS
    for point in intersection_points:
        get_or_create_node(point)
    
    print(f"Created {G.number_of_nodes()} nodes")

    # NOW SPLIT EACH LINE AT ALL RELEVANT INTERSECTION POINTS
    print("Splitting lines at intersections...")
    
    for line_info in all_lines:
        line = line_info['geometry']
        road_type = line_info['road_type']
        
        # Find all nodes that lie on or very close to this line
        split_distances = []  # (distance_along_line, node_id)
        
        for node_id, node_data in G.nodes(data=True):
            node_point = Point(node_data['pos'])
            distance_to_line = line.distance(node_point)
            
            if distance_to_line < snap_tolerance:
                # This node is on or near this line
                distance_along = line.project(node_point)
                split_distances.append((distance_along, node_id))
        
        # Sort by distance along line
        split_distances.sort(key=lambda x: x[0])
        
        # Remove duplicates and endpoints that are too close
        filtered_splits = []
        for dist, node_id in split_distances:
            # Skip if too close to start or end
            if dist < snap_tolerance or dist > (line.length - snap_tolerance):
                continue
            
            # Skip if too close to previous split
            if filtered_splits and abs(filtered_splits[-1][0] - dist) < snap_tolerance:
                continue
            
            filtered_splits.append((dist, node_id))
        
        # Get start and end nodes
        start_coords = list(line.coords)[0]
        end_coords = list(line.coords)[-1]
        start_node = get_or_create_node(start_coords)
        end_node = get_or_create_node(end_coords)
        
        # If no splits, add single edge
        if not filtered_splits:
            if start_node != end_node:
                G.add_edge(start_node, end_node,
                          length=line.length,
                          geometry=line,
                          road_type=road_type)
                
                edge_geometries[(start_node, end_node)] = line
                edge_geometries[(end_node, start_node)] = LineString(list(line.coords)[::-1])
        else:
            # Create segments between consecutive nodes
            all_nodes = [(0, start_node)] + filtered_splits + [(line.length, end_node)]
            
            for i in range(len(all_nodes) - 1):
                dist1, node1 = all_nodes[i]
                dist2, node2 = all_nodes[i + 1]
                
                if node1 == node2:
                    continue
                
                # Extract segment geometry
                # Get all coordinates between dist1 and dist2
                segment_coords = []
                
                # Add start point
                start_pt = line.interpolate(dist1)
                segment_coords.append((start_pt.x, start_pt.y))
                
                # Add intermediate points from original line
                for coord in line.coords:
                    coord_dist = line.project(Point(coord))
                    if dist1 < coord_dist < dist2:
                        segment_coords.append(coord)
                
                # Add end point
                end_pt = line.interpolate(dist2)
                segment_coords.append((end_pt.x, end_pt.y))
                
                # Create segment
                if len(segment_coords) >= 2:
                    segment_line = LineString(segment_coords)
                    
                    G.add_edge(node1, node2,
                              length=segment_line.length,
                              geometry=segment_line,
                              road_type=road_type)
                    
                    edge_geometries[(node1, node2)] = segment_line
                    edge_geometries[(node2, node1)] = LineString(segment_coords[::-1])

    print(f"Graph created: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    
    # Check connectivity
    if nx.is_connected(G):
        print("✓ Graph is fully connected!")
    else:
        components = list(nx.connected_components(G))
        print(f"✗ Graph has {len(components)} disconnected components")
        print(f"  Largest component: {len(max(components, key=len))} nodes")
        
        largest_cc = max(components, key=len)
        G = G.subgraph(largest_cc).copy()
        print(f"Using largest component: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    
    # VISUALIZE
    print("Creating visualization...")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 10))
    
    # LEFT: Original roads
    roads_2100.plot(ax=ax1, linewidth=0.5, color='blue', alpha=0.6)
    ax1.set_title(f"Original Road Network\n{len(roads_2100)} road segments", fontsize=14)
    ax1.set_xlabel("X (EPSG:2100)")
    ax1.set_ylabel("Y (EPSG:2100)")
    
    # RIGHT: Graph representation
    # Draw edges
    for (u, v), geom in edge_geometries.items():
        if (u, v) in G.edges() or (v, u) in G.edges():
            x, y = geom.xy
            ax2.plot(x, y, 'b-', linewidth=0.5, alpha=0.6)
    
    # Draw nodes
    node_positions = nx.get_node_attributes(G, 'pos')
    node_x = [pos[0] for pos in node_positions.values()]
    node_y = [pos[1] for pos in node_positions.values()]
    
    # Color nodes by degree (number of connections)
    node_degrees = dict(G.degree())
    node_colors = [node_degrees[node] for node in G.nodes()]
    
    scatter = ax2.scatter(node_x, node_y, c=node_colors, cmap='YlOrRd', 
                         s=20, alpha=0.8, edgecolors='black', linewidths=0.5)
    
    ax2.set_title(f"Graph Network\n{G.number_of_nodes()} nodes, {G.number_of_edges()} edges", 
                  fontsize=14)
    ax2.set_xlabel("X (EPSG:2100)")
    ax2.set_ylabel("Y (EPSG:2100)")
    
    # Add colorbar for node degree
    cbar = plt.colorbar(scatter, ax=ax2)
    cbar.set_label('Node Degree (# of connections)', rotation=270, labelpad=20)
    
    plt.tight_layout()
    plt.savefig('road_network_graph.png', dpi=150, bbox_inches='tight')
    print("✓ Visualization saved as 'road_network_graph.png'")
    plt.show()
    
    # STATISTICS
    print("\n" + "="*50)
    print("GRAPH STATISTICS")
    print("="*50)
    print(f"Nodes: {G.number_of_nodes()}")
    print(f"Edges: {G.number_of_edges()}")
    print(f"Average degree: {sum(dict(G.degree()).values()) / G.number_of_nodes():.2f}")
    print(f"Is connected: {nx.is_connected(G)}")
    
    degree_counts = {}
    for node, degree in G.degree():
        degree_counts[degree] = degree_counts.get(degree, 0) + 1
    
    print("\nNode degree distribution:")
    for degree in sorted(degree_counts.keys()):
        print(f"  Degree {degree}: {degree_counts[degree]} nodes")
    
    return G, edge_geometries

if __name__ == "__main__":
    G, edge_geometries = load_and_visualize_graph()


# compiling times
# 1km buffer zone -> 25s
# 2km buffer zone -> 2min20s