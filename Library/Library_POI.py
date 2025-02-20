#POI2P

import os
import numpy as np
import json
from math import inf
from tqdm import tqdm

# --- Import Numba and set up types ---
from numba import njit, types
from numba.typed import List

# Define tuple types for the forward graph:
#   (neighbor_index, travel_time, edge_start_time, edge_end_time)
edge_tuple_type = types.Tuple((types.int64, types.float64, types.float64, types.float64))

# Each heap element: (current_total_cost, current_node, current_clock_time)
heap_elem_type = types.Tuple((types.float64, types.int64, types.float64))

@njit(nogil=True)
def heap_push(heap, item):
    heap.append(item)
    i = len(heap) - 1
    while i > 0:
        parent = (i - 1) // 2
        if heap[parent][0] > heap[i][0]:
            # Swap
            temp = heap[parent]
            heap[parent] = heap[i]
            heap[i] = temp
            i = parent
        else:
            break

@njit(nogil=True)
def heap_pop(heap):
    ret = heap[0]
    last = heap.pop()
    n = len(heap)
    if n > 0:
        heap[0] = last
        i = 0
        while True:
            left = 2 * i + 1
            right = 2 * i + 2
            smallest = i
            if left < n and heap[left][0] < heap[smallest][0]:
                smallest = left
            if right < n and heap[right][0] < heap[smallest][0]:
                smallest = right
            if smallest != i:
                temp = heap[i]
                heap[i] = heap[smallest]
                heap[smallest] = temp
                i = smallest
            else:
                break
    return ret


@njit(nogil=True)
def dijkstra_multi_source(graph, initial_heap, num_nodes):
    """
    Same as your original function, but used to find minimal cost from
    any 'source' in initial_heap to every node in the graph, respecting
    schedules on transit edges.
    
    `initial_heap` is a Numba List of (cost, node, clock_time).
    
    Returns an array `dist` of best total travel times.
    """
    dist = np.full(num_nodes, np.inf)
    visited_time = np.full(num_nodes, np.inf)
    
    # The heap already contains all sources.
    heap = initial_heap
    
    # Initialize distances for the sources
    for entry in heap:
        cost, node, _ = entry
        if cost < dist[node]:
            dist[node] = cost

    while len(heap) > 0:
        current = heap_pop(heap)
        current_cost = current[0]
        node = current[1]
        clock_time = current[2]

        # If we have visited node at an earlier or same clock_time, skip
        if visited_time[node] <= clock_time:
            continue
        visited_time[node] = clock_time

        # If there's already a better cost, skip
        if current_cost > dist[node]:
            continue

        # Explore edges
        for i in range(len(graph[node])):
            neighbor, travel_time, edge_start, edge_end = graph[node][i]

            # If it's a walking edge (edge_start<0 => no schedule)
            if edge_start < 0:
                arrival_time = clock_time + travel_time
                new_cost = current_cost + travel_time
            else:
                # It's a scheduled transit edge
                # Only catch it if we arrive before the departure window
                if edge_start >= clock_time:
                    waiting = edge_start - clock_time
                    arrival_time = edge_end  # you arrive at edge_end
                    new_cost = current_cost + waiting + travel_time
                else:
                    # Missed the bus, skip
                    continue

            # Relaxation
            if new_cost < dist[neighbor]:
                dist[neighbor] = new_cost
                heap_push(heap, (new_cost, neighbor, arrival_time))

    return dist


def process_accessibility_from_pois(
    db,
    city,
    start_hours,
    max_travel_time=float('inf'),
    transit_graph_dir="matrices"
):
    """
    "Reverse the problem" by using the POIs as the multi-source origins:
      1) Build the forward graph (with schedules).
      2) For each hour, create a single multi-source heap seeded by all
         POI -> stop walking edges, at time T_depart + walk_time.
      3) Run Dijkstra once to get dist[s] = minimal cost from a POI to stop s.
      4) For each point, compute min( dist[s] + (point->stop walking) ) or direct walk.
    """
    points_collection = db["points"]
    poi_collection    = db["POI"]
    stops_collection  = db["stops"]
    edges_collection  = db["edges"]

    # 1) Load the stop_id -> index mapping
    with open(f"{transit_graph_dir}/stop_ids_{city}.json", "r") as f:
        stop_id_to_index = json.load(f)
    num_stops = len(stop_id_to_index)

    # 2) Build the forward graph (same as your original)
    from numba.typed import List as NumbaList
    transit_graph = NumbaList()
    for _ in range(num_stops):
        transit_graph.append(NumbaList.empty_list(edge_tuple_type))

    # - Insert walking edges
    stops = list(stops_collection.find({}))
    for stop in stops:
        s_id = stop["stop_id"]
        if s_id not in stop_id_to_index:
            continue
        s_idx = stop_id_to_index[s_id]

        for reachable in stop.get("reachable_stops", []):
            neighbor_id = reachable["stop_id"]
            if neighbor_id in stop_id_to_index:
                neighbor_idx = stop_id_to_index[neighbor_id]
                walking_time = float(reachable["walking_time"])
                # walking => edge_start < 0, edge_end < 0 (or same as start?)
                transit_graph[s_idx].append((neighbor_idx, walking_time, -1.0, -1.0))

    # - Insert scheduled transit edges
    edges = list(edges_collection.find({}))
    for edge in edges:
        start_stop_id = edge["stop_id"]
        end_stop_id   = edge["next_stop_id"]
        if start_stop_id not in stop_id_to_index or end_stop_id not in stop_id_to_index:
            continue
        s_idx       = stop_id_to_index[start_stop_id]
        neighbor_idx= stop_id_to_index[end_stop_id]
        travel_time = float(edge["travel_time"])
        start_time_val = float(edge["start_time"])
        end_time_val   = float(edge["end_time"])
        transit_graph[s_idx].append((neighbor_idx, travel_time, start_time_val, end_time_val))

    # 3) Gather all POIs and figure out "POI -> stop" walking times
    #    We will create a single multi-source for all POIs combined.
    poi_docs = list(poi_collection.find({}, {
        "reachable_stops.stop_id": 1, "reachable_stops.walking_time": 1,
        "reachable_points.point_id": 1, "reachable_points.walking_time": 1
    }))
    # Also track direct-walking from POI to point
    direct_walk_to_point = {}  # point_id -> minimal walking time from any POI
    for poi_doc in poi_docs:
        for rp in poi_doc.get("reachable_points", []):
            pid  = str(rp["point_id"])
            wtime= float(rp["walking_time"])
            if pid not in direct_walk_to_point or wtime < direct_walk_to_point[pid]:
                direct_walk_to_point[pid] = wtime

    # We'll build an “initial stops array” for each hour, for the multi-source Dijkstra.
    # That is: for each (POI -> stop) pair, cost=walk_time, node=stop_idx,
    #          clock_time=(T_depart + walk_time).
    # Then we run Dijkstra. We get dist[s] = minimal cost from (some POI) to stop s.

    points_projection = {"_id": 1, "reachable_stops.stop_id": 1, "reachable_stops.walking_time": 1}
    all_points = list(points_collection.find({}, points_projection))

    for hour in start_hours:
        # One run *per hour*
        T_depart = hour * 3600.0
        field_name = f"Accessibility_POI2P_{hour}"

        # Build the multi-source heap for this hour
        initial_heap = NumbaList()

        # For each POI, each reachable stop
        for poi_doc in poi_docs:
            for rs in poi_doc.get("reachable_stops", []):
                stop_id   = rs["stop_id"]
                walk_time = float(rs["walking_time"])
                if stop_id in stop_id_to_index:
                    s_idx = stop_id_to_index[stop_id]
                    cost  = walk_time  # total travel time so far
                    ctime = T_depart + walk_time  # arrival clock time at that stop
                    initial_heap.append((cost, s_idx, ctime))

        # ---- Run the multi-source Dijkstra from all POIs (for this hour) ----
        dist_array = dijkstra_multi_source(transit_graph, initial_heap, num_stops)
        # dist_array[s] = minimal travel time to get from a POI to stop `s` if departing POI at T_depart

        # Now assign to each point
        for point_doc in all_points:
            point_id = point_doc["_id"]
            best_time = float('inf')

            # 1) Direct walk from POI to this point?
            if str(point_id) in direct_walk_to_point:
                direct_w = direct_walk_to_point[str(point_id)]
                if direct_w < best_time:
                    best_time = direct_w

            # 2) Transit path: POI -> stop -> point
            #    dist_array[stop_idx] + walking_time(stop->point)
            for rs in point_doc.get("reachable_stops", []):
                stop_id    = rs["stop_id"]
                walk_time  = float(rs["walking_time"])  # from stop->point
                if stop_id in stop_id_to_index:
                    s_idx = stop_id_to_index[stop_id]
                    if dist_array[s_idx] < inf:
                        candidate = dist_array[s_idx] + walk_time
                        if candidate < best_time:
                            best_time = candidate

            # 3) Filter out if unreachable or > max_travel_time
            if best_time == float('inf') or best_time > max_travel_time:
                update_val = "Not reachable"
            else:
                update_val = best_time / 60.0  # convert to minutes

            points_collection.update_one(
                {"_id": point_id},
                {"$set": {field_name: update_val}}
            )

        print(f"Finished computing {field_name} for hour={hour}.")

    print("All done with accessibility to POI for all specified hours.")
    
    
    
###########################################################################################################################################


import folium
from pymongo import MongoClient
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
import matplotlib.ticker as mticker

def visualize_multi_hour_accessibility(points_collection, poi_collection, 
                                       hours, 
                                       html_file,
                                       thresholds,
                                       layer_opacity=0.7):
    """
    Create a single map with multiple layers. Each layer corresponds to
    "Accessibility_POI2P_{hour}", and the population data is included in the popup.
    Also adds a floating table on the bottom-right showing cumulative 
    population accessibility within specific minute thresholds.
    """
    
    def add_legend(map_object, title, class_colors, class_intervals):
        """
        Add a legend to the map (bottom-left by default).
        """
        legend_html = f"""
        <div style="position: fixed; bottom: 50px; left: 50px; width: 300px;
                    background-color: white; border: 2px solid grey; border-radius: 10px;
                    padding: 10px; z-index: 1000; font-size: 14px;">
            <h4 style="margin: 0 0 10px 0;">{title}</h4>
            <div>
        """
        for i in range(len(class_colors)):
            if i < len(class_intervals) - 1:
                lower = round(class_intervals[i], 2)
                upper = round(class_intervals[i+1], 2)
                interval = f"{lower} - {upper} min"
            else:
                interval = f"> {round(class_intervals[i], 2)} min"
            legend_html += f"""
            <div style="display: flex; align-items: center; margin-bottom: 5px;">
                <div style="width: 20px; height: 20px; background-color: {class_colors[i]};
                            border: 1px solid black; margin-right: 10px;"></div>
                <span>{interval}</span>
            </div>
            """
        legend_html += "</div></div>"
        map_object.get_root().html.add_child(folium.Element(legend_html))
    
    def get_color_and_popup(accessibility_value, class_intervals, class_colors):
        """
        Given a numeric or "Not reachable" value, returns (color, popup_text).
        """
        if accessibility_value is None or accessibility_value == "Not reachable":
            return "#999999", "Not reachable"
    
        try:
            val = float(accessibility_value)
        except:
            return "#999999", "Not reachable"
    
        if not np.isfinite(val):
            return "#999999", "Not reachable"
    
        # Loop through intervals. For the last interval, include the endpoint.
        for i in range(len(class_intervals) - 1):
            if i == len(class_intervals) - 2:
                if class_intervals[i] <= val <= class_intervals[i + 1]:
                    return class_colors[i], f"{val:.2f} min"
            else:
                if class_intervals[i] <= val < class_intervals[i + 1]:
                    return class_colors[i], f"{val:.2f} min"
    
        return "#999999", f"{val:.2f} min"
    
    def compute_cumulative_percentages(points_collection, hour, thresholds):
        """
        Computes the cumulative percentage of total population 
        for each threshold in 'thresholds' based on accessibility.
    
        Returns a list of percentages in the same order as 'thresholds'.
        """
        field_name = f"Accessibility_POI2P_{hour}"
        query_fields = {"population": 1, field_name: 1}
        points = list(points_collection.find({}, query_fields))
    
        total_population = 0
        for p in points:
            pop = p.get("population", 0)
            if isinstance(pop, (int, float)) and pop > 0:
                total_population += pop
    
        if total_population == 0:
            return [0] * len(thresholds)
    
        cumulative_pop_counts = []
        for th in thresholds:
            pop_count_at_or_below = 0
            for p in points:
                accessibility_val = p.get(field_name, None)
                pop = p.get("population", 0)
                if not isinstance(pop, (int, float)) or pop <= 0:
                    continue
                if isinstance(accessibility_val, (int, float)) and np.isfinite(accessibility_val):
                    if accessibility_val <= th:
                        pop_count_at_or_below += pop
            percentage = (pop_count_at_or_below / total_population) * 100
            cumulative_pop_counts.append(percentage)
    
        return cumulative_pop_counts
    
    def add_cumulative_table(map_object, points_collection, hours, thresholds):
        """
        Build and add an HTML table at the bottom-right of the map
        showing the cumulative percentages for each threshold, for each hour.
        """
        results_by_hour = {}
        for hr in hours:
            results_by_hour[hr] = compute_cumulative_percentages(points_collection, hr, thresholds)
    
        table_header = (
            "<th style='border: 1px solid #ddd; text-align: center;'>Threshold (min)</th>" +
            "".join([f"<th style='border: 1px solid #ddd; text-align: center;'>{hr}h</th>" for hr in hours])
        )
    
        table_rows = ""
        for i, th in enumerate(thresholds):
            row_cells = [f"<td style='border: 1px solid #ddd; text-align: center;'>{th}</td>"]
            for hr in hours:
                percentage_val = results_by_hour[hr][i]
                row_cells.append(f"<td style='border: 1px solid #ddd; text-align: center;'>{int(round(percentage_val))}%</td>")
            table_rows += "<tr>" + "".join(row_cells) + "</tr>"
    
        table_html = f"""
        <div style="position: fixed; bottom: 50px; right: 50px; width: 270px;
                    background-color: #f8f9fa; border: 2px solid #ccc; border-radius: 10px;
                    padding: 10px; z-index: 1000; font-size: 14px;
                    box-shadow: 2px 2px 8px rgba(0,0,0,0.3);">
            <h4 style="margin-top: 0; text-align: center; font-family: Arial, sans-serif; 
                    background-color: #343a40; color: #fff; padding: 6px; border-radius: 5px;">
                Accessibility Stats
            </h4>
            <table style="width: 100%; border-collapse: collapse; font-family: Arial, sans-serif;">
                <thead>
                    <tr style="background-color: #e9ecef;">
                        {table_header}
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
        """
        map_object.get_root().html.add_child(folium.Element(table_html))
    
    # -----------------------------
    # Compute dynamic class intervals using a "pretty" break approach for 10 classes.
    accessibility_values = []
    for hour in hours:
        field_name = f"Accessibility_POI2P_{hour}"
        for p in points_collection.find({}, {field_name:1}):
            val = p.get(field_name)
            try:
                val = float(val)
            except:
                continue
            if np.isfinite(val):
                accessibility_values.append(val)
    
    if accessibility_values:
        min_val = min(accessibility_values)
        max_val = max(accessibility_values)
        # Use MaxNLocator to generate "pretty" breakpoints
        locator = mticker.MaxNLocator(nbins=20)
        dynamic_breaks = locator.tick_values(min_val, max_val)
        class_intervals = list(dynamic_breaks)
        # Ensure we have exactly 21 breakpoints (for 10 classes); if not, fallback to linspace.
        if len(class_intervals) != 21:
            class_intervals = list(np.linspace(min_val, max_val, 21))
    else:
        # Fallback default intervals if no valid accessibility values found
        class_intervals = [0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, float('inf')]
    
    # Generate color mapping based on the number of intervals (10 classes)
    num_intervals = len(class_intervals) - 1
    cmap = plt.cm.get_cmap('plasma', num_intervals)
    class_colors = [mcolors.rgb2hex(cmap(i)[:3]) for i in range(cmap.N)]
    # -----------------------------
    
    # Fetch all POIs first to compute center or bounding box
    all_pois = list(poi_collection.find({}, {"geometry": 1}))
    if all_pois:
        lat_list = [poi["geometry"]["coordinates"][1] for poi in all_pois]
        lon_list = [poi["geometry"]["coordinates"][0] for poi in all_pois]
        lat_min, lat_max = min(lat_list), max(lat_list)
        lon_min, lon_max = min(lon_list), max(lon_list)
        center_lat = (lat_min + lat_max) / 2
        center_lon = (lon_min + lon_max) / 2
        map_object = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles=None)
    else:
        print("No POIs found. Falling back to the first point centroid from hexagon collection.")
        initial_point_doc = points_collection.find_one({}, {"centroid": 1})
        if not initial_point_doc:
            print("No points found in the collection either.")
            return
        initial_point = list(initial_point_doc["centroid"]["coordinates"])[::-1]
        map_object = folium.Map(location=initial_point, zoom_start=12, tiles=None)
    
    # Add a custom tile layer
    thunderforest_url = "https://tile.thunderforest.com/transport-dark/{z}/{x}/{y}.png?apikey=9594d1374f8440788cbeb2092dde1199"
    folium.TileLayer(
        tiles=thunderforest_url,
        attr='Thunderforest Transport',
        name='Thunderforest Transport',
        overlay=False,
        control=True,
    ).add_to(map_object)
    
    # Fetch base points
    points = list(points_collection.find({}, {"hexagon": 1, "centroid": 1, "population": 1}))
    
    # Create a feature group for each hour
    for hour in hours:
        field_name = f"Accessibility_POI2P_{hour}"
        layer_name = f"{hour}h"
        feature_group = folium.FeatureGroup(name=layer_name, overlay=True, control=True)
    
        # Build a dictionary for quick document lookup per hour
        points_with_hour = list(points_collection.find({}, {"hexagon": 1, field_name: 1, "population": 1}))
        point_dict_by_id = {str(p["_id"]): p for p in points_with_hour}
    
        for p in points:
            _id_str = str(p["_id"])
            doc_for_hour = point_dict_by_id.get(_id_str)
            if not doc_for_hour:
                continue
            accessibility_value = doc_for_hour.get(field_name, None)
            if accessibility_value is None or accessibility_value == "Not reachable":
                continue
            hex_coords = p["hexagon"]["coordinates"][0]
            population = doc_for_hour.get("population", "N/A")
            color, popup_val = get_color_and_popup(accessibility_value, class_intervals, class_colors)
            popup_text = f"""
                Accessibility to POI ({hour}h): {popup_val} <br>
                Population: {population}
            """
            polygon = folium.Polygon(
                locations=[[lat, lon] for lon, lat in hex_coords],
                color=None,
                fill=True,
                fill_color=color,
                fill_opacity=layer_opacity,
                popup=popup_text
            )
            polygon.add_to(feature_group)
    
        feature_group.add_to(map_object)
    
    # Add POI markers
    poi_feature_group = folium.FeatureGroup(name="POIs", overlay=True, control=True, show=False)
    for poi in all_pois:
        poi_coordinates = poi["geometry"]["coordinates"]
        folium.Marker(
            location=[poi_coordinates[1], poi_coordinates[0]],
            icon=folium.Icon(color='red', icon='info-sign'),
            popup="Point of Interest"
        ).add_to(poi_feature_group)
    poi_feature_group.add_to(map_object)
    
    # Add layer control
    folium.LayerControl().add_to(map_object)
    
    # Add legend (bottom-left)
    add_legend(map_object, "Minimum Travel Time to a POI", class_colors, class_intervals)
    
    # Add an opacity slider (top-left)
    opacity_control_html = f"""
    <div style="position: fixed; top: 10px; left: 10px; z-index: 1000; background: white; 
                padding: 10px; border-radius: 5px; border: 1px solid grey;">
        <label for='opacity'>Adjust Opacity:</label>
        <input type='range' id='opacity' min='0' max='1' step='0.1' value='{layer_opacity}' 
               oninput='changeOpacity(this.value)'/>
    </div>
    <script>
        function changeOpacity(value) {{
            document.querySelectorAll('path').forEach((path) => {{
                if (path.hasAttribute('fill-opacity')) {{
                    path.setAttribute('fill-opacity', value);
                }}
            }});
        }}
    </script>
    """
    map_object.get_root().html.add_child(folium.Element(opacity_control_html))
    
    # Define thresholds for cumulative table (in minutes)
    #thresholds = [30, 45, 60, 90]
    add_cumulative_table(map_object, points_collection, hours, thresholds)
    
    # Optionally, fit map bounds around all POIs:
    # if all_pois:
    #     map_object.fit_bounds([(lat_min, lon_min), (lat_max, lon_max)])
    
    map_object.save(html_file)
    print(f"Accessibility map saved to {html_file} with population stats at the bottom-right.")
