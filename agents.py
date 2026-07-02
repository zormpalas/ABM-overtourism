# agents.py
from mesa import Agent, Model
from mesa.space import ContinuousSpace
import numpy as np
import math
from scipy.spatial import KDTree

class ResidentAgent(Agent):
    def __init__(self, model):
        super().__init__(model)
        self.happiness = 0.7  # Initial happiness level
    
    def step(self):
        # Only update happiness during waking hours (8-24)
        # During sleeping hours (0-8), happiness stays at reset value
        current_hour = self.model.current_hour
        if current_hour >= 8:
            self._update_happiness()
    
    def _update_happiness(self):
        """Update happiness based on number of nearby tourists."""
        # Get all neighbors within 30m
        neighbors = self.model.space.get_neighbors(self.pos, radius=30, include_center=False)
        
        # Count only tourists
        tourist_count = sum(1 for agent in neighbors if agent.__class__.__name__ == "TouristAgent")
        
        # Update happiness based on tourist count
        if tourist_count == 0:
            self.happiness += 0.02  # Increase happiness when alone
        elif tourist_count <= 3:
            self.happiness += 0.005  # Small increase with few tourists
        elif tourist_count <= 10:
            self.happiness -= 0.01  # Decrease with moderate crowd
        else:
            self.happiness -= 0.03  # Large decrease with many tourists
        
        # Clamp happiness between 0 and 1
        self.happiness = max(0.0, min(1.0, self.happiness))
    
    def reset_happiness(self):
        """Reset happiness to default value."""
        self.happiness = 0.7

class TouristAgent(Agent):
    def __init__(self, model, noise, pollution, edge, dist, home_hotel):
        super().__init__(model)
        self.noise = noise
        self.pollution = pollution
        self.satisfaction = 0.7  # Initial satisfaction level
        
        # Graph-based movement attributes
        self.current_edge = edge          # Tuple (node_u, node_v)
        self.pos_on_edge = dist           # Distance along edge from node_u
        self.target_node = edge[1]        # Currently moving toward this node
        self.speed = np.random.uniform(10, 20)  # meters per step

        # Behavioral attributes
        self.home_hotel = home_hotel      # Hotel POI object
        # Start as "wandering" so they teleport to hotel on first step
        self.state = "wandering"          # States: "sleeping", "walking_to_tier_a/b/c", "visiting_tier_a/b/c", "casual_wandering"
        
        # POI targeting system
        self.primary_target = None        # Current main target POI (can be tier A, B, or C)
        self.secondary_target = None      # Stored Tier A POI when detoured to visit Tier B/C first
        self.visited_pois = set()         # Set of visited POI IDs (never resets across days)
        
        # Visit probabilities (decrease after each visit)
        self.tier_b_stop_probability = 0.30  # 30% chance to stop at cafe/restaurant
        self.tier_c_stop_probability = 0.20  # 20% chance to stop at shop
        
        # Visit tracking
        self.visit_end_hour = None        # Hour when current visit ends
        self.visit_end_min = None         # Minute when current visit ends
        
    def step(self):
        """Main behavior loop for tourist."""
        current_hour = self.model.current_hour
        current_min = self.model.current_min
        
        # SLEEPING (0-8)
        if (0 <= current_hour < 8):
            if self.state != "sleeping":
                # Go to hotel if not already there
                self._teleport_to_hotel()
                self.state = "sleeping"
                # Reset satisfaction when going to sleep
                self.satisfaction = 0.7
            return
        
        # WAKE UP AT 8:00 AM (transition from sleeping to active)
        if current_hour >= 8 and self.state == "sleeping":
            self._initialize_daily_targets()
            # Don't return - continue with the rest of the step to start moving immediately
        
        # VISITING STATE (tourist is inside a POI)
        if self.state in ["visiting_tier_a", "visiting_tier_b", "visiting_tier_c"]:
            # Check if visit is over
            if self._is_visit_complete():
                self._exit_poi()
            return
        
        # ACTIVE STATES (tourist is walking or wandering)
        if self.state in ["walking_to_tier_a", "walking_to_tier_b", "walking_to_tier_c"]:
            # Forward vision: scan for Tier B/C POIs in 30m cone ahead
            self._forward_vision_scan()
            
            # Check if arrived at primary target
            if self.primary_target is not None:
                if self._has_arrived_at_target():
                    self._handle_arrival_at_target()
                    return
        
        # CASUAL WANDERING STATE (no specific target, explore opportunistically)
        if self.state == "casual_wandering":
            # Scan for any interesting POIs nearby
            self._casual_poi_scan()
        
        # Move along current edge, and choose new edge at intersections
        self._move_on_graph()
        
        # Update satisfaction based on crowding
        self._update_satisfaction()

    def _initialize_daily_targets(self):
        """
        Initialize targets when waking up at 8am.
        Scan 100m radius for POIs and set initial target.
        """
        # Scan 100m for Tier A POIs
        tier_a_pois = self._scan_pois_in_radius(100, tier_filter="A")
        
        if tier_a_pois:
            # Found Tier A - pick randomly and head toward it
            self.primary_target = np.random.choice(tier_a_pois)
            self.state = "walking_to_tier_a"
            print(f"Tourist {self.unique_id} woke up, heading to Tier A: {self.primary_target['name']}")
        else:
            # No Tier A found - look for Tier B
            tier_b_pois = self._scan_pois_in_radius(100, tier_filter="B")
            if tier_b_pois:
                self.primary_target = np.random.choice(tier_b_pois)
                self.state = "walking_to_tier_b"
                print(f"Tourist {self.unique_id} woke up, heading to Tier B: {self.primary_target['name']}")
            else:
                # No targets found - casual wandering
                self.state = "casual_wandering"
                print(f"Tourist {self.unique_id} woke up, casual wandering (no POIs nearby)")

    def _scan_pois_in_radius(self, radius, tier_filter=None):
        """
        Scan for POIs within radius, optionally filtered by tier.
        Returns list of unvisited POIs matching the criteria.
        """
        if self.model.poi_kdtree is None:
            return []

        # Single spatial query instead of looping all POIs
        indices = self.model.poi_kdtree.query_ball_point(self.pos, radius)

        available_pois = []
        for idx in indices:
            poi = self.model.all_pois[idx]
            if poi['id'] in self.visited_pois:
                continue
            if tier_filter and poi['tier'] != tier_filter:
                continue
            available_pois.append(poi)

        return available_pois

    def _forward_vision_scan(self):
        """
        Scan for Tier B/C POIs in 30m forward cone (30 degrees).
        This is called when tourist is walking toward a target.
        If attractive POI found, tourist may detour to visit it first.
        """
        if self.primary_target is None or self.secondary_target is not None:
            return
        
        # Get direction to primary target
        target_x = self.primary_target['geometry'].x - self.model.xmin
        target_y = self.primary_target['geometry'].y - self.model.ymin
        
        dx_target = target_x - self.pos[0]
        dy_target = target_y - self.pos[1]
        dist_target = math.sqrt(dx_target**2 + dy_target**2)
        
        if dist_target < 0.01:
            return
        
        # Normalize direction vector
        dx_target /= dist_target
        dy_target /= dist_target
        
        # Ask KDTree for only the POIs within 30m
        nearby_indices = self.model.poi_kdtree.query_ball_point(self.pos, 30)

        for idx in nearby_indices:
            poi = self.model.all_pois[idx]
            # Skip visited POIs
            if poi['id'] in self.visited_pois:
                continue
            # Only looking for Tier B/C (not Tier A when already heading to Tier A)
            if poi['tier'] == "A":
                continue
            
            # Calculate position in model coords
            poi_x = poi['geometry'].x - self.model.xmin
            poi_y = poi['geometry'].y - self.model.ymin
            
            # Check distance (must be within 30m)
            dx_poi = poi_x - self.pos[0]
            dy_poi = poi_y - self.pos[1]
            dist_poi = math.sqrt(dx_poi**2 + dy_poi**2)
            
            if dist_poi < 0.01:
                continue
            
            # Normalize direction to POI
            dx_poi /= dist_poi
            dy_poi /= dist_poi
            
            # Calculate angle between target direction and POI direction
            dot_product = dx_target * dx_poi + dy_target * dy_poi
            dot_product = max(-1.0, min(1.0, dot_product))  # Clamp to avoid math errors
            angle_rad = math.acos(dot_product)
            angle_deg = math.degrees(angle_rad)
            
            # Check if POI is within 30 degree cone
            if angle_deg <= 30:
                # POI is in forward vision - consider visiting it
                if self._consider_poi_detour(poi):
                    # Tourist decided to detour - swap targets
                    return  # Exit after first detour decision

    def _consider_poi_detour(self, poi):
        """
        Roll dice to decide if tourist detours to visit this POI.
        If yes, swap primary/secondary targets and change state.
        Returns True if detour happens, False otherwise.
        """
        # PREVENT DETOURING TO THE SAME POI AS PRIMARY TARGET AND DOUBLE-DETOURS
        if poi['id'] == self.primary_target['id'] or self.secondary_target is not None:
            return False
        
        # Determine probability based on tier
        if poi['tier'] == "B":
            probability = self.tier_b_stop_probability
        elif poi['tier'] == "C":
            probability = self.tier_c_stop_probability
        else:
            return False
        
        # Roll the dice
        if np.random.random() < probability:
            # Decide to detour!
            print(f"Tourist {self.unique_id} detouring from {self.primary_target['name']} to visit {poi['name']}")
            
            # Store current target as secondary
            self.secondary_target = self.primary_target
            
            # Set POI as new primary target
            self.primary_target = poi
            
            # Update state based on POI tier
            if poi['tier'] == "B":
                self.state = "walking_to_tier_b"
            elif poi['tier'] == "C":
                self.state = "walking_to_tier_c"
            
            return True
        
        return False

    def _casual_poi_scan(self):
        """
        Scan for any Tier A/B/C POIs in 100m radius during casual wandering.
        Prioritize Tier A, then Tier B, then Tier C.
        """
        # Scan for Tier A first (highest priority)
        tier_a_pois = self._scan_pois_in_radius(100, tier_filter="A")
        
        if tier_a_pois:
            # Always go to closest Tier A
            closest = min(tier_a_pois, key=lambda p: self._distance_to_poi(p))
            self.primary_target = closest
            self.state = "walking_to_tier_a"
            print(f"Tourist {self.unique_id} found Tier A during wandering: {closest['name']}")
            return
        
        # No Tier A, check Tier B
        tier_b_pois = self._scan_pois_in_radius(100, tier_filter="B")
        
        if tier_b_pois:
            closest = min(tier_b_pois, key=lambda p: self._distance_to_poi(p))
            if np.random.random() < self.tier_b_stop_probability:
                self.primary_target = closest
                self.state = "walking_to_tier_b"
                print(f"Tourist {self.unique_id} found Tier B during wandering: {closest['name']}")
                return
        
        # No Tier A or B, check Tier C
        tier_c_pois = self._scan_pois_in_radius(100, tier_filter="C")
        
        if tier_c_pois:
            closest = min(tier_c_pois, key=lambda p: self._distance_to_poi(p))
            if np.random.random() < self.tier_c_stop_probability:
                self.primary_target = closest
                self.state = "walking_to_tier_c"
                print(f"Tourist {self.unique_id} found Tier C during wandering: {closest['name']}")

    def _distance_to_poi(self, poi):
        """Calculate distance from tourist to POI (in model coordinates)."""
        poi_x = poi['geometry'].x - self.model.xmin
        poi_y = poi['geometry'].y - self.model.ymin
        return math.sqrt((self.pos[0] - poi_x)**2 + (self.pos[1] - poi_y)**2)

    def _has_arrived_at_target(self):
        """Check if tourist has arrived at primary target POI."""
        if self.primary_target is None:
            return False
        
        dist = self._distance_to_poi(self.primary_target)
        return dist < 15  # Within 15 meters

    def _handle_arrival_at_target(self):
        """
        Handle arrival at primary target POI.
        Start visit, update state, reduce probabilities.
        """
        poi = self.primary_target
        current_hour = self.model.current_hour
        current_min = self.model.current_min
        
        tier = poi['tier']
        
        # Check if can enter (for Tier A with closing time)
        if tier == "A":
            if current_hour >= 18:
                # Too late to enter Tier A (last entry at 6pm)
                print(f"Tourist {self.unique_id} arrived too late at {poi['name']} (after 6pm)")
                self.primary_target = None
                self.state = "casual_wandering"
                return
            
            # Visit Tier A for 3 hours
            visit_duration_min = 180
            self.state = "visiting_tier_a"
            print(f"Tourist {self.unique_id} entering Tier A: {poi['name']} for 3 hours")
            
        elif tier == "B":
            # Visit Tier B for 1 hour
            visit_duration_min = 60
            self.state = "visiting_tier_b"
            # Reduce probability after visiting
            self.tier_b_stop_probability = max(0.15, self.tier_b_stop_probability - 0.15)
            print(f"Tourist {self.unique_id} visiting Tier B: {poi['name']} for 1 hour")
            
        else:  # Tier C
            # Visit Tier C for 20 minutes
            visit_duration_min = 20
            self.state = "visiting_tier_c"
            # Reduce probability after visiting
            self.tier_c_stop_probability = max(0.05, self.tier_c_stop_probability - 0.05)
            print(f"Tourist {self.unique_id} visiting Tier C: {poi['name']} for 20 min")
        
        # Mark POI as visited (never visit again)
        self.visited_pois.add(poi['id'])
        
        # Calculate end time of visit
        total_min = current_hour * 60 + current_min + visit_duration_min
        self.visit_end_hour = total_min // 60
        self.visit_end_min = total_min % 60

    def _is_visit_complete(self):
        """Check if current visit is complete."""
        current_hour = self.model.current_hour
        current_min = self.model.current_min
        
        # Forced exit from Tier A at 8pm (closing time)
        if self.state == "visiting_tier_a":
            if current_hour >= 20:
                print(f"Tourist {self.unique_id} forced to leave Tier A at 8pm closing")
                return True
        
        # Check normal visit end time
        current_total_min = current_hour * 60 + current_min
        end_total_min = self.visit_end_hour * 60 + self.visit_end_min
        
        return current_total_min >= end_total_min

    def _exit_poi(self):
        """
        Exit POI after visit completion.
        Handle target swapping if there's a secondary target.
        Check if secondary target is still within reasonable range.
        """        
        print(f"Tourist {self.unique_id} finished visiting {self.primary_target['name']}")
        
        # Check if there's a secondary target to restore
        if self.secondary_target is not None:
            # Check if secondary target is still within range (300m extended range)
            dist_to_secondary = self._distance_to_poi(self.secondary_target)
            
            if dist_to_secondary <= 300:
                # Secondary target is still within extended range - restore it
                print(f"Tourist {self.unique_id} resuming journey to {self.secondary_target['name']} (distance: {dist_to_secondary:.1f}m)")
                self.primary_target = self.secondary_target
                self.secondary_target = None
                
                # Set appropriate state based on restored target tier
                if self.primary_target['tier'] == "A":
                    self.state = "walking_to_tier_a"
                elif self.primary_target['tier'] == "B":
                    self.state = "walking_to_tier_b"
                else:
                    self.state = "walking_to_tier_c"
            else:
                # Secondary target is too far away - tourist has wandered too far
                print(f"Tourist {self.unique_id} lost track of {self.secondary_target['name']} (too far: {dist_to_secondary:.1f}m), now wandering")
                self.primary_target = None
                self.secondary_target = None
                self.state = "casual_wandering"
        else:
            # No secondary target - go to casual wandering
            self.primary_target = None
            self.state = "casual_wandering"
            print(f"Tourist {self.unique_id} now casual wandering")

    def _update_satisfaction(self):
        """Update satisfaction based on number of nearby tourists."""
        # Get all neighbors within 30m
        neighbors = self.model.space.get_neighbors(self.pos, radius=30, include_center=False)
        
        # Count only tourists (exclude self)
        tourist_count = sum(1 for agent in neighbors if agent.__class__.__name__ == "TouristAgent")
        
        # Update satisfaction based on tourist count
        if tourist_count <= 5:
            self.satisfaction += 0.01  # Increase satisfaction with few tourists
        elif tourist_count <= 15:
            pass  # No change with moderate crowd
        else:
            self.satisfaction -= 0.02  # Decrease satisfaction in overcrowded areas
        
        # Clamp satisfaction between 0 and 1
        self.satisfaction = max(0.0, min(1.0, self.satisfaction))

    def reset_satisfaction(self):
        """Reset satisfaction to default value."""
        self.satisfaction = 0.7

    def _teleport_to_hotel(self):
        """Teleport tourist directly to their home hotel."""
        hotel_x = self.home_hotel['geometry'].x
        hotel_y = self.home_hotel['geometry'].y
        
        # Convert to model coordinates
        x = hotel_x - self.model.xmin
        y = hotel_y - self.model.ymin
        
        # Move agent to hotel
        self.model.space.move_agent(self, (x, y))

        # Find nearest edge to hotel and reset movement attributes
        nearest_node = self._find_nearest_node_to_poi(self.home_hotel)
        neighbors = list(self.model.road_graph.neighbors(nearest_node))
        
        if neighbors:
            # Set edge starting from hotel's nearest node
            self.current_edge = (nearest_node, neighbors[0])
            self.target_node = neighbors[0]
            self.pos_on_edge = 0

    def _find_nearest_node_to_poi(self, poi):
        """Find the nearest graph node to a POI."""
        poi_x = poi['geometry'].x
        poi_y = poi['geometry'].y
        
        min_dist = float('inf')
        nearest_node = None
        
        for node_id, node_data in self.model.road_graph.nodes(data=True):
            node_x, node_y = node_data['pos']
            dist = np.sqrt((poi_x - node_x)**2 + (poi_y - node_y)**2)
            if dist < min_dist:
                min_dist = dist
                nearest_node = node_id
        
        return nearest_node

    def _move_on_graph(self):
        """Move along edges using hybrid pathfinding."""
        # Get current edge geometry (in EPSG:2100 coordinates)
        edge_geom = self.model.edge_geometries[self.current_edge]
        edge_length = edge_geom.length
        
        # Move forward
        self.pos_on_edge += self.speed
        
        # Check if we reached the target node (intersection)
        if self.pos_on_edge >= edge_length:
            # We've reached an intersection - choose next road
            overshoot = self.pos_on_edge - edge_length
            self._choose_next_edge()                # sets pos_on_edge = 0 internally
            self.pos_on_edge = overshoot            # override with carried-over distance
            edge_geom = self.model.edge_geometries[self.current_edge]
        
        # Get current geometric position on the edge (in EPSG:2100)
        pt = edge_geom.interpolate(self.pos_on_edge)
        
        # pt.x and pt.y are in EPSG:2100, need to convert to model coords
        x = pt.x - self.model.xmin
        y = pt.y - self.model.ymin

        # Clamp to space bounds to prevent out-of-bounds errors
        x = max(0, min(self.model.width - 0.01, x))
        y = max(0, min(self.model.height - 0.01, y))
        
        # Update position in space
        self.model.space.move_agent(self, (x, y))

    def _choose_next_edge(self):
        """Choose next edge using hybrid pathfinding."""
        # Get neighbors of target node
        neighbors = list(self.model.road_graph.neighbors(self.target_node))
        
        if not neighbors:
            # Dead end - turn around
            self.current_edge = (self.current_edge[1], self.current_edge[0])
            self.target_node = self.current_edge[1]
            self.pos_on_edge = 0
            return
        
        # Remove the node we just came from (avoid immediate backtracking)
        current_start_node = self.current_edge[0]
        valid_neighbors = [n for n in neighbors if n != current_start_node]
        
        # If no valid neighbors (only way is back), then go back
        if not valid_neighbors:
            self.current_edge = (self.current_edge[1], self.current_edge[0])
            self.target_node = self.current_edge[1]
            self.pos_on_edge = 0
            return
        
        # HYBRID PATHFINDING
        if self.primary_target is not None:
            # We have a target - use smart pathfinding
            next_node = self._choose_edge_toward_target(valid_neighbors)
        else:
            # No target - random walk
            next_node = np.random.choice(valid_neighbors)
        
        # Update edge and position
        self.current_edge = (self.target_node, next_node)
        self.target_node = next_node
        self.pos_on_edge = 0

    def _choose_edge_toward_target(self, valid_neighbors):
        """
        Choose edge using hybrid pathfinding toward target.
        Combines direction alignment and road attractiveness.
        If target is far away (>200m), prioritize direction over attractiveness.
        """
        target_x = self.primary_target['geometry'].x
        target_y = self.primary_target['geometry'].y
        
        current_node_pos = self.model.road_graph.nodes[self.target_node]['pos']
        
        # Direction to target
        dx_target = target_x - current_node_pos[0]
        dy_target = target_y - current_node_pos[1]
        dist_target = math.sqrt(dx_target**2 + dy_target**2)
        
        if dist_target < 0.01:
            return np.random.choice(valid_neighbors)
        
        dx_target /= dist_target
        dy_target /= dist_target
        
        # Determine if target is far away (needs more focus on direction)
        target_is_far = dist_target > 200
        
        # Score each neighbor edge
        scores = []
        for neighbor in valid_neighbors:
            neighbor_pos = self.model.road_graph.nodes[neighbor]['pos']
            
            # Direction to neighbor
            dx_neighbor = neighbor_pos[0] - current_node_pos[0]
            dy_neighbor = neighbor_pos[1] - current_node_pos[1]
            dist_neighbor = math.sqrt(dx_neighbor**2 + dy_neighbor**2)
            
            if dist_neighbor < 0.01:
                scores.append(0)
                continue
            
            dx_neighbor /= dist_neighbor
            dy_neighbor /= dist_neighbor
            
            # Dot product (how aligned with target direction)
            alignment = dx_target * dx_neighbor + dy_target * dy_neighbor
            alignment = max(0, alignment)  # Only consider forward directions
            
            # Get edge attractiveness (pre-computed score)
            edge = (self.target_node, neighbor)
            attractiveness = self.model.road_graph[self.target_node][neighbor].get('attractiveness', 1)
            
            # Combined score (direction + attractiveness)
            # If target is far, weight direction more heavily
            if target_is_far:
                # Far target: 80% direction, 20% attractiveness
                score = (alignment * 0.8) + (attractiveness * 0.2 / 10)  # normalize attractiveness
            else:
                # Near target: balanced approach
                score = alignment * attractiveness
            
            scores.append(score)
        
        # Reduce exploration factor when target is far
        exploration_chance = 0.05 if target_is_far else 0.15
        
        if np.random.random() < exploration_chance:
            return np.random.choice(valid_neighbors)
        
        # Probabilistic selection based on scores
        total_score = sum(scores)
        if total_score == 0:
            return np.random.choice(valid_neighbors)
        
        probabilities = [s / total_score for s in scores]
        return np.random.choice(valid_neighbors, p=probabilities)

class CityModel(Model):
    def __init__(self, width, height, resident_points=None, tourist_points=None,
                 tourist_noises=None, tourist_pollutions=None,
                 n_residents=10, n_tourists=10, seed=None, xmin=0, ymin=0,
                 road_graph=None, edge_geometries=None, hotels=None, all_pois=None):
        super().__init__(seed=seed)
        self.space = ContinuousSpace(width, height, torus=False)
        self.xmin = xmin
        self.ymin = ymin
        self.width = width
        self.height = height
        
        # Store road network graph
        self.road_graph = road_graph
        self.edge_geometries = edge_geometries

        # Time tracking (0-23 hours, 0-59 minutes)
        self.current_hour = 0
        self.current_min = 0

        # Store hotels and all POIs
        self.hotels = hotels
        self.all_pois = all_pois if all_pois else []
        self.hotel_occupancy = {i: 0 for i in range(len(self.hotels))}

        # Build spatial index for fast POI lookups — replaces O(n) loops each step
        if self.all_pois:
            poi_coords = np.array([
                [p['geometry'].x - self.xmin, p['geometry'].y - self.ymin]
                for p in self.all_pois
            ])
            self.poi_kdtree = KDTree(poi_coords)
        else:
            self.poi_kdtree = None
            
        # create residents
        if resident_points is not None:
            for pt in resident_points:
                agent = ResidentAgent(self)
                self.space.place_agent(agent, pt)
        
        # create tourists with hotel assignments
        if tourist_points is not None:
            for ((pos, edge, dist), noise, pol) in zip(tourist_points, 
            tourist_noises, tourist_pollutions):
                hotel = self._assign_hotel()
                agent = TouristAgent(self, noise, pol, edge, dist, hotel)
                self.space.place_agent(agent, pos)
    
    def _assign_hotel(self):
        """Assign tourist to a random hotel with available capacity."""
        max_capacity = 50  # Each hotel can hold 50 tourists
        
        available_hotels = [
            i for i, occupancy in self.hotel_occupancy.items() 
            if occupancy < max_capacity
        ]
        
        if not available_hotels:
            # All hotels full, assign to random hotel anyway
            hotel_idx = np.random.randint(len(self.hotels))
        else:
            hotel_idx = np.random.choice(available_hotels)
        
        self.hotel_occupancy[hotel_idx] += 1
        return self.hotels[hotel_idx]
    
    def step(self):
        # Advance time by 10 minutes
        self.current_min += 10
        if self.current_min >= 60:
            self.current_min = 0
            self.current_hour = (self.current_hour + 1) % 24
        
        # Check if it's midnight (start of new day) - reset happiness and satisfaction
        if self.current_hour == 0 and self.current_min == 0:
            self._reset_daily_attributes()
        
        # random activation
        self.agents.shuffle_do("step")
    
    def _reset_daily_attributes(self):
        """Reset happiness and satisfaction at midnight."""
        for agent in self.agents:
            if agent.__class__.__name__ == "ResidentAgent":
                agent.reset_happiness()
            elif agent.__class__.__name__ == "TouristAgent":
                agent.reset_satisfaction()
                # Reset probabilities
                agent.tier_b_stop_probability = 0.30
                agent.tier_c_stop_probability = 0.20
                # Note: visited_pois does NOT reset (never revisit POIs)
        print("Reset happiness, satisfaction, and probabilities at midnight")
    
    def skip_to_morning(self):
        """Skip to 8:00 AM and teleport all tourists to hotels. Also reset happiness and satisfaction."""
        self.current_hour = 8
        self.current_min = 0
        
        # Reset all happiness and satisfaction levels
        for agent in self.agents:
            if agent.__class__.__name__ == "ResidentAgent":
                agent.reset_happiness()
            elif agent.__class__.__name__ == "TouristAgent":
                agent.reset_satisfaction()
                agent.tier_b_stop_probability = 0.30
                agent.tier_c_stop_probability = 0.20
                # Teleport to hotel
                agent._teleport_to_hotel()
                agent.state = "sleeping"
        
        print("Skipped to 8:00 AM - all tourists at hotels, happiness and satisfaction reset")
    
    def generate_heatmap(self, attribute='noise', cell_size=10):
        """
        Generate a heatmap grid for noise or pollution.
        
        Args:
            attribute: 'noise' or 'pollution'
            cell_size: size of each grid cell in meters (default 10m)
        
        Returns:
            Dictionary with grid data
        """
        # Calculate grid dimensions
        n_cols = int(np.ceil(self.width / cell_size))
        n_rows = int(np.ceil(self.height / cell_size))
        
        # Initialize grid with zeros
        grid = np.zeros((n_rows, n_cols))
        
        # Get only tourist agents
        tourists = [agent for agent in self.agents 
                   if agent.__class__.__name__ == "TouristAgent"]
        
        # For each tourist, add their contribution to the grid
        for tourist in tourists:
            # Skip sleeping tourists (they contribute 0)
            if tourist.state == "sleeping":
                continue
            
            # Get tourist position in model coordinates
            x_model, y_model = tourist.pos
            
            # Determine which cell the tourist is in
            col = int(x_model / cell_size)
            row = int(y_model / cell_size)
            
            # Make sure we're within bounds
            if 0 <= row < n_rows and 0 <= col < n_cols:
                # Get the attribute value (noise or pollution)
                value = getattr(tourist, attribute)
                
                # Add full value to current cell
                grid[row, col] += value
                
                # Add reduced value (30%) to neighboring cells (within 20m)
                # Check 8 neighboring cells
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0:
                            continue  # Skip the center cell
                        
                        neighbor_row = row + dr
                        neighbor_col = col + dc
                        
                        # Check if neighbor is within bounds
                        if 0 <= neighbor_row < n_rows and 0 <= neighbor_col < n_cols:
                            grid[neighbor_row, neighbor_col] += value * 0.3
        
        # Create corner coordinates for each cell in EPSG:2100
        # We'll send corners so frontend can convert to WGS84
        cell_corners = []
        for row in range(n_rows):
            row_corners = []
            for col in range(n_cols):
                # Calculate corners in model coordinates
                x_min_model = col * cell_size
                x_max_model = (col + 1) * cell_size
                y_min_model = row * cell_size
                y_max_model = (row + 1) * cell_size
                
                # Convert to EPSG:2100 (add back xmin, ymin)
                x_min_2100 = x_min_model + self.xmin
                x_max_2100 = x_max_model + self.xmin
                y_min_2100 = y_min_model + self.ymin
                y_max_2100 = y_max_model + self.ymin
                
                row_corners.append({
                    'x_min': x_min_2100,
                    'x_max': x_max_2100,
                    'y_min': y_min_2100,
                    'y_max': y_max_2100
                })
            cell_corners.append(row_corners)
        
        return {
            'grid': grid.tolist(),
            'n_rows': n_rows,
            'n_cols': n_cols,
            'cell_size': cell_size,
            'cell_corners': cell_corners,
            'attribute': attribute
        }