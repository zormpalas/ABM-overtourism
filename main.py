# main.py
from contextlib import asynccontextmanager 
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from agents import CityModel

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.ops import unary_union
from shapely.geometry import LineString, Point

from pyproj import Transformer
transformer_2100_to_4326 = Transformer.from_crs("EPSG:2100", "EPSG:4326", always_xy=True)

import networkx as nx
from shapely import STRtree

def model_to_wgs84(x_model, y_model, xmin, ymin):
    """Converts (model_x, model_y) into WGS84 (lon, lat)"""
    x_2100 = x_model + xmin
    y_2100 = y_model + ymin
    lon, lat = transformer_2100_to_4326.transform(x_2100, y_2100)
    return lon, lat
    
DATA_PATH = "data/greece-251102-free.shp"

def load_gis_model(city_name="Πάτρα", buffer_m=1_000, max_roads=5000):
    """Runs GIS pipeline and returns model data."""
    # LOAD CITY
    places = gpd.read_file(DATA_PATH, layer="gis_osm_places_free_1")

    # Filter by name
    city = places[places["name"] == city_name]

    if city.empty:
        print(f"Available cities with '{city_name}' in name:")
        similar = places[places["name"].str.contains(city_name, case=False, na=False)]
        print(similar[["name", "fclass", "population"]].to_string())
        raise ValueError(f"City '{city_name}' not found.")

    # If multiple matches, pick the largest by population or most important by fclass
    if len(city) > 1:
        print(f"Found {len(city)} places named '{city_name}':")
        print(city[["name", "fclass", "population"]])
        
        fclass_priority = {'city': 0, 'town': 1, 'village': 2, 'hamlet': 3}
        city = city.copy()
        city['priority'] = city['fclass'].map(lambda x: fclass_priority.get(x, 99))
        city = city.sort_values(['priority', 'population'], ascending=[True, False])
        city = city.head(1)
        
        print(f"Selected: {city.iloc[0]['name']} ({city.iloc[0]['fclass']}, pop: {city.iloc[0]['population']})")
    
    city_2100 = city.to_crs(2100)

    # Create buffer
    buffer_2100 = city_2100.buffer(buffer_m)
    buffer_4326 = buffer_2100.to_crs(4326)

    minx, miny, maxx, maxy = buffer_4326.total_bounds

    print(f"Bounding box: ({minx:.4f}, {miny:.4f}) to ({maxx:.4f}, {maxy:.4f})")
    print(f"Area: ~{((maxx-minx)*111*69):.2f} km²")

    # LOAD ROADS WITH BBOX FILTER
    roads = gpd.read_file(DATA_PATH, layer="gis_osm_roads_free_1", bbox=(minx, miny, maxx, maxy))
    roads_2100 = roads.to_crs(2100)
    print(f"Total roads before filtering: {len(roads_2100)}")

    # FILTER TO PEDESTRIAN-ACCESSIBLE ROADS
    pedestrian_types = ['footway', 'path', 'pedestrian', 'steps', 'residential', 
                       'living_street', 'service', 'unclassified', 'tertiary', 
                       'secondary', 'primary']
    
    if 'fclass' in roads_2100.columns:
        roads_2100 = roads_2100[roads_2100['fclass'].isin(pedestrian_types)]
    
    print(f"Total roads after filtering: {len(roads_2100)}")

    # SAFETY CHECK
    if len(roads_2100) > max_roads:
        print(f"WARNING: {len(roads_2100)} roads exceed the {max_roads} limit — proceeding anyway. "
              f"This may take a while.")

    if len(roads_2100) > max_roads:
        print(f"WARNING: Too many roads ({len(roads_2100)} > {max_roads})")
        print("This will take a very long time to process.")
        response = input("Continue anyway? (yes/no): ")
        if response.lower() != 'yes':
            raise ValueError("Processing cancelled by user")

    # LOAD POIs
    print("Loading POIs...")
    pois = gpd.read_file(DATA_PATH, layer="gis_osm_pois_free_1", bbox=(minx, miny, maxx, maxy))
    pois_2100 = pois.to_crs(2100)
    
    print("Available POI types:", pois_2100['fclass'].unique()[:20])
    
    # Define POI tiers
    tier_a_types = ['museum', 'attraction', 'monument', 'memorial', 'artwork', 
                    'castle', 'ruins', 'archaeological_site', 'battlefield', 'fort',
                    'picnic_site', 'viewpoint', 'zoo', 'theme_park', 'mall', 'department_store']
    
    tier_b_types = ['cafe', 'restaurant', 'fast_food', 'food_court', 'bar', 'pub', 'biergarten']
    
    tier_c_types = ['shop', 'convenience', 'supermarket', 'clothes', 'books', 
                    'gift_shop', 'bakery', 'butcher', 'greengrocer']
    
    # Categorize all POIs
    all_pois_list = []
    poi_id_counter = 0
    
    for idx, row in pois_2100.iterrows():
        fclass = row.get('fclass', 'unknown')
        
        # Determine tier
        tier = None
        if fclass in tier_a_types:
            tier = "A"
        elif fclass in tier_b_types:
            tier = "B"
        elif fclass in tier_c_types:
            tier = "C"
        
        if tier:  # Only include POIs we care about
            raw_name = row.get('name')
            if pd.isna(raw_name):
                name = f'POI_{poi_id_counter}'
            else:
                name = raw_name

            all_pois_list.append({
                'id': poi_id_counter,
                'name': name,
                'geometry': row.geometry,
                'fclass': fclass,
                'tier': tier
            })
            poi_id_counter += 1
    
    print(f"Total POIs loaded: {len(all_pois_list)}")
    print(f"  Tier A: {sum(1 for p in all_pois_list if p['tier'] == 'A')}")
    print(f"  Tier B: {sum(1 for p in all_pois_list if p['tier'] == 'B')}")
    print(f"  Tier C: {sum(1 for p in all_pois_list if p['tier'] == 'C')}")
    
    # Filter hotels (for sleeping)
    hotel_types = ['hotel', 'hostel', 'motel', 'guest_house']
    hotels_gdf = pois_2100[pois_2100['fclass'].isin(hotel_types)]
    print(f"Found {len(hotels_gdf)} hotels")
    
    if len(hotels_gdf) < 3:
        print("Not enough hotels, adding accommodation POIs...")
        accommodation = pois_2100[
            (pois_2100['fclass'].str.contains('accommodation|hotel', case=False, na=False)) &
            (~pois_2100.index.isin(hotels_gdf.index))
        ]
        
        if len(accommodation) > 0:
            hotels_gdf = gpd.GeoDataFrame(pd.concat([hotels_gdf, accommodation], ignore_index=True))
            print(f"Total hotels: {len(hotels_gdf)}")

        if len(hotels_gdf) < 3:
            print("Using tourist POIs as hotels...")
            tourist_pois = pois_2100[
                (pois_2100['fclass'].isin(['attraction', 'viewpoint', 'museum', 'restaurant', 'cafe'])) &
                (~pois_2100.index.isin(hotels_gdf.index))
            ].head(5 - len(hotels_gdf))
            
            if len(tourist_pois) > 0:
                hotels_gdf = gpd.GeoDataFrame(pd.concat([hotels_gdf, tourist_pois], ignore_index=True))
                print(f"Total hotels: {len(hotels_gdf)}")

    # Convert hotels to dictionaries
    hotels_list = []
    hotel_id = 0
    for idx, row in hotels_gdf.iterrows():
        raw_name = row.get('name')
        if pd.isna(raw_name):
            name = f'Hotel_{hotel_id}'
        else:
            name = raw_name
            
        hotels_list.append({
            'id': f'hotel_{hotel_id}',
            'name': name,
            'geometry': row.geometry,
            'fclass': row.get('fclass', 'hotel')
        })
        hotel_id += 1

    # MODEL COORDINATE SYSTEM
    xmin, ymin, xmax, ymax = roads_2100.total_bounds
    width = xmax - xmin
    height = ymax - ymin

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

    # COLLECT ALL LINE GEOMETRIES
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

    # FIND INTERSECTIONS
    print("Finding intersections...")
    intersection_points = set()
    
    for i, line1_info in enumerate(all_lines):
        line1 = line1_info['geometry']
        coords1 = list(line1.coords)
        intersection_points.add(coords1[0])
        intersection_points.add(coords1[-1])
        
        for j, line2_info in enumerate(all_lines):
            if i >= j:
                continue
            
            line2 = line2_info['geometry']
            
            if line1.intersects(line2):
                intersection = line1.intersection(line2)
                
                if intersection.geom_type == 'Point':
                    pt = intersection
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
                    for pt in intersection.geoms:
                        intersection_points.add((pt.x, pt.y))
                
                elif intersection.geom_type == 'LineString':
                    overlap_coords = list(intersection.coords)
                    intersection_points.add(overlap_coords[0])
                    intersection_points.add(overlap_coords[-1])
    
    print(f"Found {len(intersection_points)} intersection points")
    
    # CREATE NODES
    for point in intersection_points:
        get_or_create_node(point)
    
    print(f"Created {G.number_of_nodes()} nodes")

    # Build spatial index of nodes once — avoids O(n_lines × n_nodes) loop
    node_id_list = list(G.nodes())
    node_point_list = [Point(G.nodes[n]['pos']) for n in node_id_list]
    node_strtree = STRtree(node_point_list)

    # SPLIT LINES
    print("Splitting lines at intersections...")
    
    for line_info in all_lines:
        line = line_info['geometry']
        road_type = line_info['road_type']
        
        split_distances = []

        # Query only nodes within snap_tolerance of this line (replaces full node scan)
        candidate_idxs = node_strtree.query(line.buffer(snap_tolerance))
        
        for idx in candidate_idxs:
            node_id = node_id_list[idx]
            node_point = node_point_list[idx]
            distance_to_line = line.distance(node_point)

            if distance_to_line < snap_tolerance:
                distance_along = line.project(node_point)
                split_distances.append((distance_along, node_id))
        
        split_distances.sort(key=lambda x: x[0])
        
        filtered_splits = []
        for dist, node_id in split_distances:
            if dist < snap_tolerance or dist > (line.length - snap_tolerance):
                continue
            if filtered_splits and abs(filtered_splits[-1][0] - dist) < snap_tolerance:
                continue
            filtered_splits.append((dist, node_id))
        
        start_coords = list(line.coords)[0]
        end_coords = list(line.coords)[-1]
        start_node = get_or_create_node(start_coords)
        end_node = get_or_create_node(end_coords)
        
        if not filtered_splits:
            if start_node != end_node:
                G.add_edge(start_node, end_node, length=line.length, geometry=line, road_type=road_type)
                edge_geometries[(start_node, end_node)] = line
                edge_geometries[(end_node, start_node)] = LineString(list(line.coords)[::-1])
        else:
            all_nodes = [(0, start_node)] + filtered_splits + [(line.length, end_node)]
            
            for i in range(len(all_nodes) - 1):
                dist1, node1 = all_nodes[i]
                dist2, node2 = all_nodes[i + 1]
                
                if node1 == node2:
                    continue
                
                segment_coords = []
                start_pt = line.interpolate(dist1)
                segment_coords.append((start_pt.x, start_pt.y))
                
                for coord in line.coords:
                    coord_dist = line.project(Point(coord))
                    if dist1 < coord_dist < dist2:
                        segment_coords.append(coord)
                
                end_pt = line.interpolate(dist2)
                segment_coords.append((end_pt.x, end_pt.y))
                
                if len(segment_coords) >= 2:
                    segment_line = LineString(segment_coords)
                    
                    G.add_edge(node1, node2, length=segment_line.length, geometry=segment_line, road_type=road_type)
                    edge_geometries[(node1, node2)] = segment_line
                    edge_geometries[(node2, node1)] = LineString(segment_coords[::-1])

    print(f"Graph created: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # CALCULATE ROAD ATTRACTIVENESS
    print("Calculating road attractiveness scores...")
    
    # Define base scores for road types
    road_type_scores = {
        'pedestrian': 5,
        'footway': 4,
        'living_street': 3,
        'residential': 2,
        'tertiary': 1,
        'secondary': 1,
        'primary': 0
    }
    
    for (u, v) in G.edges():
        edge_geom = edge_geometries[(u, v)]
        road_type = G[u][v].get('road_type', 'unknown')
        
        # Create 30m buffer
        buffer = edge_geom.buffer(30)
        
        # Count POIs within buffer
        n_tier_a = 0
        n_tier_b = 0
        n_tier_c = 0
        
        for poi in all_pois_list:
            if buffer.contains(poi['geometry']):
                if poi['tier'] == "A":
                    n_tier_a += 1
                elif poi['tier'] == "B":
                    n_tier_b += 1
                elif poi['tier'] == "C":
                    n_tier_c += 1
        
        # Calculate attractiveness score
        base_score = road_type_scores.get(road_type, 0)
        poi_score = (n_tier_a * 10) + (n_tier_b * 3) + (n_tier_c * 1)
        total_score = base_score + poi_score
        
        # Store in both directions
        G[u][v]['attractiveness'] = total_score
        if G.has_edge(v, u):
            G[v][u]['attractiveness'] = total_score
    
    print("Road attractiveness calculated")

    # Check connectivity
    if nx.is_connected(G):
        print("Graph is fully connected!")
    else:
        print("Graph not fully connected")

    def random_points_on_graph_edges(G, edge_geometries, n=10):
        results = []
        edges = list(G.edges())

        for _ in range(n):
            u, v = edges[np.random.randint(len(edges))]
            line = edge_geometries[(u, v)]
            dist = np.random.uniform(0, line.length)
            pt = line.interpolate(dist)
            results.append((pt, (u, v), dist))
        return results

    resident_pts_2100 = random_points_on_graph_edges(G, edge_geometries, n=500)
    tourist_pts_2100  = random_points_on_graph_edges(G, edge_geometries, n=1000)

    # Shift coords into model space
    resident_shifted = [(p.x - xmin, p.y - ymin) for (p, line, dist) in resident_pts_2100]
    tourist_shifted  = [((p.x - xmin, p.y - ymin), edge, dist) for (p, edge, dist) in tourist_pts_2100]

    # Agent attributes
    tourist_noises = [float(np.random.uniform(1,2)) for _ in tourist_pts_2100]
    tourist_pollutions = [float(np.random.uniform(1,2)) for _ in tourist_pts_2100]

    return {
        "width": width,
        "height": height,
        "resident_points": resident_shifted,
        "tourist_points": tourist_shifted,
        "tourist_noises": tourist_noises,
        "tourist_pollutions": tourist_pollutions,
        "xmin": xmin,
        "ymin": ymin,
        "road_graph" : G,
        "edge_geometries": edge_geometries,
        "hotels" : hotels_list,
        "all_pois": all_pois_list
    }

# GLOBAL MODEL INSTANCE
model = None
step_counter = 0

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    global model, step_counter

    # Wrap startup in try/except so a missing shapefile or bad city name produces a readable error
    try:
        data = load_gis_model()
        model = CityModel(
            width=data["width"],
            height=data["height"],
            resident_points=data["resident_points"],
            tourist_points=data["tourist_points"],
            tourist_noises=data["tourist_noises"],
            tourist_pollutions=data["tourist_pollutions"],
            xmin=data["xmin"],
            ymin=data["ymin"],
            road_graph=data["road_graph"],
            edge_geometries=data["edge_geometries"],
            hotels=data["hotels"],
            all_pois=data["all_pois"]
        )
        step_counter = 0
        print(f"Model created with {len(data['all_pois'])} POIs and {len(data['hotels'])} hotels.")
    except Exception as e:
        print(f"FATAL ERROR during model initialization: {e}")
        raise  # Re-raise so the server fails fast with a visible traceback
    
    yield  # Server is running

app = FastAPI(lifespan=lifespan) 

# Helper to serialise agent list (shared by /state, /step, /skip_to_morning)
def _serialise_agents():
    agents_out = []
    for agent in model.agents:
        x_model, y_model = agent.pos
        lon, lat = model_to_wgs84(x_model, y_model, model.xmin, model.ymin)

        if agent.__class__.__name__ == "ResidentAgent":
            agents_out.append({
                "type": "resident",
                "x": lon,
                "y": lat,
                "happiness": float(agent.happiness)
            })
        else:
            agents_out.append({
                "type": "tourist",
                "x": lon,
                "y": lat,
                "noise": float(agent.noise),
                "pollution": float(agent.pollution),
                "state": agent.state,
                "satisfaction": float(agent.satisfaction),
                "primary_target": agent.primary_target['name'] if agent.primary_target else None,
                "secondary_target": agent.secondary_target['name'] if agent.secondary_target else None,
            })
    return agents_out

@app.get("/state")
def get_state():
    global model, step_counter
    
    hotels_out = []
    for hotel in model.hotels:
        lon, lat = transformer_2100_to_4326.transform(hotel['geometry'].x, hotel['geometry'].y)
        hotels_out.append({
            "name": hotel['name'],
            "x": lon,
            "y": lat,
            "type": hotel['fclass']
        })

    pois_out = []
    for poi in model.all_pois:
        lon, lat = transformer_2100_to_4326.transform(poi['geometry'].x, poi['geometry'].y)
        pois_out.append({
            "name": poi['name'],
            "x": lon,
            "y": lat,
            "tier": poi['tier'],
            "type": poi['fclass']
        })

    return {
        "step": step_counter,
        "hour": model.current_hour,
        "min": model.current_min,
        "agents": _serialise_agents(),
        "hotels": hotels_out,
        "pois": pois_out
    }

@app.post("/step")
def step_once():
    global model, step_counter

    model.step()
    step_counter += 1

    return {
        "step": step_counter,
        "hour": model.current_hour,
        "min": model.current_min,
        "agents": _serialise_agents()
    }

@app.post("/skip_to_morning")
def skip_to_morning():
    global model, step_counter

    model.skip_to_morning()
    step_counter += 1

    return {
        "step": step_counter,
        "hour": model.current_hour,
        "min": model.current_min,
        "agents": _serialise_agents()
    }

@app.get("/heatmap/noise")
def get_noise_heatmap():
    global model
    
    print("Generating noise heatmap...")
    heatmap_data = model.generate_heatmap(attribute='noise', cell_size=10)
    
    cells_wgs84 = []
    for row_corners in heatmap_data['cell_corners']:
        row_wgs84 = []
        for cell in row_corners:
            lon_min, lat_min = transformer_2100_to_4326.transform(cell['x_min'], cell['y_min'])
            lon_max, lat_max = transformer_2100_to_4326.transform(cell['x_max'], cell['y_max'])
            
            row_wgs84.append({
                'lon_min': lon_min,
                'lon_max': lon_max,
                'lat_min': lat_min,
                'lat_max': lat_max
            })
        cells_wgs84.append(row_wgs84)
    
    return {
        'grid': heatmap_data['grid'],
        'n_rows': heatmap_data['n_rows'],
        'n_cols': heatmap_data['n_cols'],
        'cell_size': heatmap_data['cell_size'],
        'cells': cells_wgs84,
        'attribute': 'noise'
    }

@app.get("/heatmap/pollution")
def get_pollution_heatmap():
    global model
    
    print("Generating pollution heatmap...")
    heatmap_data = model.generate_heatmap(attribute='pollution', cell_size=10)
    
    cells_wgs84 = []
    for row_corners in heatmap_data['cell_corners']:
        row_wgs84 = []
        for cell in row_corners:
            lon_min, lat_min = transformer_2100_to_4326.transform(cell['x_min'], cell['y_min'])
            lon_max, lat_max = transformer_2100_to_4326.transform(cell['x_max'], cell['y_max'])
            
            row_wgs84.append({
                'lon_min': lon_min,
                'lon_max': lon_max,
                'lat_min': lat_min,
                'lat_max': lat_max
            })
        cells_wgs84.append(row_wgs84)
    
    return {
        'grid': heatmap_data['grid'],
        'n_rows': heatmap_data['n_rows'],
        'n_cols': heatmap_data['n_cols'],
        'cell_size': heatmap_data['cell_size'],
        'cells': cells_wgs84,
        'attribute': 'pollution'
    }

@app.get("/distribution/happiness")
def get_happiness_distribution():
    global model
    
    print("Calculating happiness distribution...")
    
    bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    counts = [0] * (len(bins) - 1)
    
    residents = [agent for agent in model.agents 
                if agent.__class__.__name__ == "ResidentAgent"]
    
    for resident in residents:
        happiness = resident.happiness
        for i in range(len(bins) - 1):
            if bins[i] <= happiness < bins[i + 1]:
                counts[i] += 1
                break
        if happiness == 1.0:
            counts[-1] += 1
    
    labels = [f"{bins[i]:.1f}-{bins[i+1]:.1f}" for i in range(len(bins) - 1)]
    
    return {
        'labels': labels,
        'counts': counts,
        'total_residents': len(residents)
    }

@app.get("/distribution/satisfaction")
def get_satisfaction_distribution():
    global model
    
    print("Calculating satisfaction distribution...")
    
    bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    counts = [0] * (len(bins) - 1)
    
    tourists = [agent for agent in model.agents 
               if agent.__class__.__name__ == "TouristAgent"]
    
    for tourist in tourists:
        satisfaction = tourist.satisfaction
        for i in range(len(bins) - 1):
            if bins[i] <= satisfaction < bins[i + 1]:
                counts[i] += 1
                break
        if satisfaction == 1.0:
            counts[-1] += 1
    
    labels = [f"{bins[i]:.1f}-{bins[i+1]:.1f}" for i in range(len(bins) - 1)]
    
    return {
        'labels': labels,
        'counts': counts,
        'total_tourists': len(tourists)
    }

app.mount("/", StaticFiles(directory="static", html=True), name="static")