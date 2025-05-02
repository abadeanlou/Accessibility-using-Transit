import pandas as pd
import zipfile
import io

def load_gtfs_data(gtfs_zip_path):
    with zipfile.ZipFile(gtfs_zip_path, 'r') as z:
        # Load each file directly into pandas
        stops = pd.read_csv(io.TextIOWrapper(z.open("stops.txt")),low_memory=False)
        trips = pd.read_csv(io.TextIOWrapper(z.open("trips.txt")),low_memory=False)
        routes = pd.read_csv(io.TextIOWrapper(z.open("routes.txt")),low_memory=False)
        calendar_dates = pd.read_csv(io.TextIOWrapper(z.open("calendar_dates.txt")),low_memory=False)

        # Check if calendar.txt exists before trying to load it
        if "calendar.txt" in z.namelist():
            calendar = pd.read_csv(io.TextIOWrapper(z.open("calendar.txt")),low_memory=False)
        else:
            calendar = None
            print("calendar.txt does not exist in the ZIP file.")
        print("All Necessary files have been loaded")
    return stops, trips, routes, calendar_dates, calendar


###########################################################################################################################################


import pandas as pd
from tqdm import tqdm
import geopandas as gpd
from shapely.geometry import Point, shape

def process_and_save_gtfs_to_mongo(db, calendar_dates, routes, trips, stops, calendar=None, boundary_shapefile=None):
    # Collections
    calendar_dates_collection = db["calendar_dates"]
    routes_collection = db["routes"]
    trips_collection = db["trips"]
    stops_collection = db["stops"]
    calendar_collection = db["calendar"]

    # Drop collections if they exist
    print("Dropping existing collections if they exist...")
    calendar_dates_collection.drop()
    routes_collection.drop()
    trips_collection.drop()
    stops_collection.drop()
    calendar_collection.drop()
    print("Collections dropped successfully.")

    # Function to save a DataFrame to MongoDB in batches
    def save_to_mongo(df, collection, selected_columns, batch_size=1000, transform_row=None):
        print(f"Saving {collection.name} to MongoDB...")
        batch = []
        for _, row in tqdm(df[selected_columns].iterrows(), total=len(df)):
            record = row.to_dict()
            if transform_row:
                record = transform_row(record)
            batch.append(record)
            if len(batch) == batch_size:
                collection.insert_many(batch)
                batch = []
        if batch:
            collection.insert_many(batch)
        print(f"{collection.name} saved to MongoDB.")

    # Transform function for stops: convert lat/lon into a GeoJSON Point
    def transform_stops_row(row):
        row["location"] = {
            "type": "Point",
            "coordinates": [row.pop("stop_lon"), row.pop("stop_lat")]
        }
        return row

    # Process and save calendar_dates.txt
    calendar_dates["service_id"] = calendar_dates["service_id"].astype(str)
    calendar_dates["date"] = pd.to_datetime(calendar_dates["date"], format='%Y%m%d', errors='coerce')
    calendar_dates["exception_type"] = calendar_dates["exception_type"].astype(int)
    save_to_mongo(calendar_dates, calendar_dates_collection, ["service_id", "date", "exception_type"])

    # Process and save routes.txt
    routes["route_id"] = routes["route_id"].astype(str)
    routes["agency_id"] = routes["agency_id"].astype(str)
    routes["route_short_name"] = routes["route_short_name"].astype(str)
    routes["route_long_name"] = routes["route_long_name"].astype(str)
    routes["route_type"] = routes["route_type"].astype(int)
    save_to_mongo(routes, routes_collection, ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"])

    # Process and save trips.txt
    trips["route_id"] = trips["route_id"].astype(str)
    trips["service_id"] = trips["service_id"].astype(str)
    trips["trip_id"] = trips["trip_id"].astype(str)
    save_to_mongo(trips, trips_collection, ["route_id", "service_id", "trip_id"])

    # If a boundary shapefile is provided, filter stops based on it.
    if boundary_shapefile is not None:
        print("Filtering stops based on the boundary shapefile...")
        # Read the boundary shapefile and combine its geometries
        boundary_gdf = gpd.read_file(boundary_shapefile)
        boundary_union = boundary_gdf.unary_union
        # Create a geometry column from stop_lon and stop_lat
        stops["geometry"] = stops.apply(lambda row: Point(row["stop_lon"], row["stop_lat"]), axis=1)
        # Filter stops that lie within the boundary
        stops = stops[stops["geometry"].apply(lambda p: p.within(boundary_union))]
        # Drop the temporary geometry column
        stops = stops.drop(columns=["geometry"])
        print(f"Number of stops after filtering: {len(stops)}")
    else:
        print("No boundary shapefile provided; no stops filtering applied.")

    # Process and save stops.txt
    stops["stop_id"] = stops["stop_id"].astype(str)
    stops["stop_name"] = stops["stop_name"].fillna("").astype(str)
    stops["stop_lat"] = stops["stop_lat"].astype(float)
    stops["stop_lon"] = stops["stop_lon"].astype(float)
    save_to_mongo(stops, stops_collection, ["stop_id", "stop_name", "stop_lat", "stop_lon"], transform_row=transform_stops_row)

    # Process and save calendar.txt if it exists
    if calendar is not None:
        calendar["service_id"] = calendar["service_id"].astype(str)
        calendar["monday"] = calendar["monday"].astype(int)
        calendar["tuesday"] = calendar["tuesday"].astype(int)
        calendar["wednesday"] = calendar["wednesday"].astype(int)
        calendar["thursday"] = calendar["thursday"].astype(int)
        calendar["friday"] = calendar["friday"].astype(int)
        calendar["saturday"] = calendar["saturday"].astype(int)
        calendar["sunday"] = calendar["sunday"].astype(int)
        calendar["start_date"] = pd.to_datetime(calendar["start_date"], format='%Y%m%d', errors='coerce')
        calendar["end_date"] = pd.to_datetime(calendar["end_date"], format='%Y%m%d', errors='coerce')
        save_to_mongo(calendar, calendar_collection, [
            "service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "start_date", "end_date"
        ])

###########################################################################################################################################


import pandas as pd
from pymongo import MongoClient, InsertOne
from tqdm import tqdm
import zipfile
import logging

def process_gtfs_data(db, gtfs_zip_path, specific_date=None):
    # ------------------------------------------------------------------------------
    # Configure logging
    # ------------------------------------------------------------------------------
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    edges_collection = db["edges"]
    stops_collection = db["stops"]

    # Function to load stop_times.txt
    def load_gtfs_stop_times(file_path):
        logger.info("Loading stop_times from GTFS zip file...")
        with zipfile.ZipFile(file_path, 'r') as z:
            with z.open('stop_times.txt') as f:
                return pd.read_csv(
                    f,
                    dtype={
                        "trip_id": str,
                        "arrival_time": str,
                        "departure_time": str,
                        "stop_id": str,
                        "stop_sequence": int
                    },
                    low_memory=False,
                    usecols=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]
                )

    # Function to fetch data from MongoDB
    def fetch_mongo_data(collection_name, projection):
        logger.info(f"Fetching data from MongoDB collection: {collection_name}...")
        cursor = db[collection_name].find({}, projection)
        return pd.DataFrame(list(cursor))

    # Function to preprocess data
    def preprocess_data(stop_times, trips, calendar, calendar_dates, specific_date=None):
        logger.info("Processing data...")

        if not calendar.empty:
            calendar["start_date"] = pd.to_datetime(calendar["start_date"], format="%Y%m%d", errors="coerce")
            calendar["end_date"] = pd.to_datetime(calendar["end_date"], format="%Y%m%d", errors="coerce")

            expanded_rows = []
            for _, row in calendar.iterrows():
                if pd.isnull(row["start_date"]) or pd.isnull(row["end_date"]):
                    continue

                current_date = row["start_date"]
                while current_date <= row["end_date"]:
                    if row[current_date.strftime("%A").lower()]:
                        expanded_rows.append({"service_id": row["service_id"], "date": current_date})
                    current_date += pd.Timedelta(days=1)

            daily_service = pd.DataFrame(expanded_rows, columns=["service_id", "date"])
        else:
            logger.warning("Calendar is empty; using only calendar_dates.")
            daily_service = pd.DataFrame(columns=["service_id", "date"])

        calendar_dates["date"] = pd.to_datetime(calendar_dates["date"], errors="coerce")
        additions = calendar_dates[calendar_dates["exception_type"] == 1]
        removals = calendar_dates[calendar_dates["exception_type"] == 2]

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=FutureWarning)
            daily_service = pd.concat([daily_service, additions[['service_id', 'date']]], ignore_index=True)


        if not removals.empty:
            daily_service = daily_service.merge(
                removals[["service_id", "date"]],
                on=["service_id", "date"],
                how="left",
                indicator=True
            )
            daily_service = daily_service[daily_service["_merge"] == "left_only"].drop(columns=["_merge"])

        daily_service.drop_duplicates(inplace=True)

        if specific_date is not None:
            daily_service = daily_service[daily_service["date"] == specific_date]

        trips = trips.astype(str)
        stop_times["arrival_time"] = pd.to_datetime(stop_times["arrival_time"], format='%H:%M:%S', errors='coerce')
        stop_times["departure_time"] = pd.to_datetime(stop_times["departure_time"], format='%H:%M:%S', errors='coerce')

        logger.info("Merging trips with daily service data...")
        trips_with_dates = trips.merge(daily_service, on="service_id", how="inner")

        stop_times["date"] = stop_times["trip_id"].map(trips_with_dates.set_index("trip_id")["date"])
        stop_times["route_id"] = stop_times["trip_id"].map(trips_with_dates.set_index("trip_id")["route_id"])

        stop_times.dropna(subset=["date", "route_id"], inplace=True)

        logger.info("Sorting stop_times and calculating travel times...")
        stop_times = stop_times.sort_values(["trip_id", "stop_sequence"])
        stop_times["next_stop_id"] = stop_times.groupby("trip_id")["stop_id"].shift(-1)
        stop_times["travel_time"] = (
            (stop_times.groupby("trip_id")["arrival_time"].shift(-1) - stop_times["departure_time"]).dt.total_seconds()
        )
        stop_times.dropna(subset=["next_stop_id", "travel_time"], inplace=True)

        logger.info("Preparing edge data...")
        edges = stop_times[[
            "stop_id", "next_stop_id", "trip_id", "route_id", "date", "travel_time"
        ]].copy()

        edges["start_time"] = (
            pd.to_datetime(edges["date"].astype(str) + ' ' + stop_times["departure_time"].dt.strftime('%H:%M:%S'))
            - pd.to_datetime(edges["date"].astype(str))
        ).dt.total_seconds()

        edges["end_time"] = edges["start_time"] + edges["travel_time"]

        return edges

    # Function to save edges to MongoDB
    def save_edges_to_mongo(edges):
        logger.info("Saving edges to MongoDB in batches...")
        batch_size = 20000
        total_batches = (len(edges) + batch_size - 1) // batch_size

        edges_batches = (edges.iloc[i:i + batch_size].to_dict("records")
                         for i in range(0, len(edges), batch_size))

        logger.setLevel(logging.ERROR)
        with tqdm(total=total_batches, desc="Saving Batches", unit="batch", dynamic_ncols=True) as progress_bar:
            for batch in edges_batches:
                try:
                    edges_collection.bulk_write([InsertOne(edge) for edge in batch], ordered=False)
                except Exception as e:
                    logger.error(f"Error during bulk write: {e}")
                finally:
                    progress_bar.update(1)

        logger.setLevel(logging.INFO)
        logger.info(f"All batches processed successfully.")

    # Function to clean stops collection
    def clean_stops_and_edges():
        logger.info("Cleaning stops not available in edges...")
        # Get valid stops from edges (from both fields)
        valid_stops_from_edges = set(edges_collection.distinct("stop_id")) | set(edges_collection.distinct("next_stop_id"))
        stops_removed = stops_collection.delete_many({"stop_id": {"$nin": list(valid_stops_from_edges)}})
        logger.info(f"Number of stops removed: {stops_removed.deleted_count}")
    
        logger.info("Cleaning edges referencing invalid stops...")
        # Get valid stops from the (now updated) stops collection
        valid_stops = set(stops_collection.distinct("stop_id"))
        edges_removed = edges_collection.delete_many({
            "$or": [
                {"stop_id": {"$nin": list(valid_stops)}},
                {"next_stop_id": {"$nin": list(valid_stops)}}
            ]
        })
        logger.info(f"Number of edges removed: {edges_removed.deleted_count}")

    def find_busiest_date(stop_times, trips, calendar, calendar_dates, logger):
        """
        Return the calendar date that would generate the most edges.
        """
        logger.info("Scanning all dates to find the busiest one…")
    
        # ―― Build the daily service table (same logic as preprocess_data) ――
        calendar["start_date"] = pd.to_datetime(calendar["start_date"], format="%Y%m%d", errors="coerce")
        calendar["end_date"]   = pd.to_datetime(calendar["end_date"],   format="%Y%m%d", errors="coerce")
    
        expanded = []
        for _, row in calendar.iterrows():
            if pd.isna(row["start_date"]) or pd.isna(row["end_date"]):
                continue
            d = row["start_date"]
            while d <= row["end_date"]:
                if row[d.strftime("%A").lower()]:
                    expanded.append({"service_id": row["service_id"], "date": d})
                d += pd.Timedelta(days=1)
    
        daily_service = pd.DataFrame(expanded, columns=["service_id", "date"])
    
        calendar_dates["date"] = pd.to_datetime(calendar_dates["date"], errors="coerce")
        daily_service = pd.concat(
            [daily_service, calendar_dates[calendar_dates["exception_type"] == 1][["service_id", "date"]]],
            ignore_index=True
        ).drop_duplicates()
    
        # Remove cancellations
        removals = calendar_dates[calendar_dates["exception_type"] == 2][["service_id", "date"]]
        if not removals.empty:
            daily_service = daily_service.merge(
                removals, on=["service_id", "date"], how="left", indicator=True
            ).query('_merge == "left_only"').drop(columns="_merge")
    
        # ―― Attach trips to their operating days ――
        trips_dates = trips.merge(daily_service, on="service_id", how="inner")
    
        # ―― Bring in stop_times only to COUNT potential edges ――
        quick = stop_times.merge(
            trips_dates[["trip_id", "date"]], on="trip_id", how="inner"
        )
    
        #  edges for a trip‑date = rows – 1
        quick["ones"] = 1
        # rows per trip‑date
        per_td = quick.groupby(["trip_id", "date"])["ones"].sum()           # rows
        edges_per_td = per_td - 1                                           # rows – 1
        # sum across trips -> edges per date
        edges_per_date = edges_per_td.groupby("date").sum()
    
        busiest_date = edges_per_date.idxmax()
        max_edges    = edges_per_date.max()
    
        logger.info(f"Busiest date is {busiest_date.date()} with {max_edges:,} edges.")
        return busiest_date

    # Workflow execution
    stop_times = load_gtfs_stop_times(gtfs_zip_path)
    trips = fetch_mongo_data("trips", {"_id": 0, "trip_id": 1, "service_id": 1, "route_id": 1})
    calendar = fetch_mongo_data("calendar", {"_id": 0})
    calendar_dates = fetch_mongo_data("calendar_dates", {"_id": 0, "date": 1, "service_id": 1, "exception_type": 1})

    if specific_date is None:
        specific_date = find_busiest_date(
            stop_times, trips, calendar, calendar_dates, logger
        )
    else:
        specific_date = pd.to_datetime(specific_date)
        logger.info(f"Processing only the supplied date: {specific_date.date()}")

    # Build edges for the chosen day
    edges = preprocess_data(
        stop_times, trips, calendar, calendar_dates, specific_date
    )

    # Insert to MongoDB in 20 000‑row batches
    save_edges_to_mongo(edges)

    # Remove orphan stops and edges
    clean_stops_and_edges()

    logger.info("Edges have been stored on the database.")



###########################################################################################################################################


import geopandas as gpd
import numpy as np
from shapely.geometry import Point, Polygon
from pymongo import MongoClient
from tqdm import tqdm
import math
import folium
from pyproj import Transformer
from IPython.display import display

def process_hexagonal_grid(db, aoi_path, grid_edge_km):
    """
    Processes an AOI shapefile, creates a hexagonal grid, saves it to MongoDB, and visualizes it.

    Args:
        db (pymongo.database.Database): MongoDB database connection.
        aoi_path (str): Path to the AOI shapefile.
        grid_edge_km (float): Length of hexagon edge in kilometers.
    """
    points_collection = db["points"]  # Collection for storing points
    points_collection.drop()

    def create_hexagonal_grid(aoi, grid_edge_km):
        # Reproject AOI to UTM for accurate calculations
        aoi_projected = aoi.to_crs(aoi.estimate_utm_crs())

        # Get bounding box of the AOI in projected CRS
        minx, miny, maxx, maxy = aoi_projected.total_bounds

        # Hexagon edge length in meters
        edge_length = grid_edge_km * 1000

        # Calculate step sizes for the hexagonal grid
        dx = 3 / 2 * edge_length
        dy = math.sqrt(3) * edge_length

        # Generate hexagonal grid points
        points = []
        for x in np.arange(minx, maxx, dx):
            for y in np.arange(miny, maxy, dy):
                row_offset = (int((x / dx)) % 2) * (dy / 2)
                points.append(Point(x, y + row_offset))

        # Create hexagons around each point
        hexagons = []
        for point in points:
            hexagon = create_hexagon(point.x, point.y, edge_length)
            if aoi_projected.geometry.unary_union.intersects(hexagon):  # Include partial overlaps
                hexagons.append(hexagon)

        # Create GeoDataFrame for the hexagons
        hex_grid = gpd.GeoDataFrame(geometry=hexagons, crs=aoi_projected.crs)
        return hex_grid

    def create_hexagon(x_center, y_center, edge_length):
        # Create a hexagon geometry around a given center point
        angles = np.linspace(0, 2 * np.pi, 7)
        return Polygon([
            (x_center + edge_length * math.cos(angle), y_center + edge_length * math.sin(angle))
            for angle in angles
        ])

    def save_points_to_mongodb(hex_grid):
        print("Saving hexagons and centroids to MongoDB...")
        hex_grid_wgs84 = hex_grid.to_crs(epsg=4326)

        batch_size = 1000
        for i in tqdm(range(0, len(hex_grid_wgs84), batch_size), desc="Saving Hexagons"):
            batch = hex_grid_wgs84.iloc[i:i + batch_size]
            records = []
            for hexagon in batch.geometry:
                centroid = hexagon.centroid
                record = {
                    "centroid": {"type": "Point", "coordinates": [centroid.x, centroid.y]},
                    "hexagon": {"type": "Polygon", "coordinates": [list(hexagon.exterior.coords)]}
                }
                records.append(record)
            points_collection.insert_many(records)

        print("All hexagons and centroids saved to MongoDB.")

    def visualize_hexagons_inline(hex_grid, aoi):
        # Convert hexagons to WGS84 for visualization
        hex_grid_wgs84 = hex_grid.to_crs(epsg=4326)
        aoi_wgs84 = aoi.to_crs(epsg=4326)

        # Calculate the center of the bounding box for better centering
        minx, miny, maxx, maxy = aoi_wgs84.total_bounds
        center_lat = (miny + maxy) / 2
        center_lon = (minx + maxx) / 2

        # Create a Folium map
        m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

        # Add AOI boundary to the map
        folium.GeoJson(aoi_wgs84, name="Area of Interest").add_to(m)

        # Add hexagons to the map
        for _, row in hex_grid_wgs84.iterrows():
            geo_json = folium.GeoJson(row["geometry"], style_function=lambda x: {
                "color": "blue",
                "weight": 1,
                "fillColor": "blue",
                "fillOpacity": 0.2
            })
            geo_json.add_to(m)

        # Display the map
        display(m)

    # Load AOI shapefile
    print("Loading Area of Interest (AOI) boundary shapefile...")
    aoi = gpd.read_file(aoi_path)

    # Create hexagonal grid
    print("Creating hexagonal grid...")
    hex_grid = create_hexagonal_grid(aoi, grid_edge_km)

    # Save hexagons and centroids to MongoDB in WGS84
    save_points_to_mongodb(hex_grid)

    # Visualize hexagons inline
    # print("Visualizing hexagonal grid...")
    # visualize_hexagons_inline(hex_grid, aoi)

    print("Hexagonal grid processing complete.")




###########################################################################################################################################



import os
import pymongo
import requests
from tqdm import tqdm
from multiprocessing import cpu_count
import time
import concurrent.futures

# Configure logging without file logging or printing ERROR/CRITICAL messages to console
import logging

logger = logging.getLogger()
if logger.hasHandlers():
    logger.handlers.clear()

# Configure console handler to exclude ERROR and CRITICAL logs
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

# Filter to exclude ERROR and CRITICAL logs from console
class ExcludeErrorsFilter(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.ERROR

console_handler.addFilter(ExcludeErrorsFilter())
logger.addHandler(console_handler)

# Set the logging level for the logger
logger.setLevel(logging.INFO)

STOPS_COLLECTION = "stops"
POINTS_COLLECTION = "points"
EDGES_COLLECTION = "edges" 

MAX_WORKERS = cpu_count()

def process_points(db, osrm_base_url, max_walking_distance, max_walking_time):
    stops_collection = db[STOPS_COLLECTION]
    points_collection = db[POINTS_COLLECTION]
    edges_collection = db[EDGES_COLLECTION]  

    # Remove existing 'reachable_stops' from all points
    points_collection.update_many({}, {"$unset": {"reachable_stops": ""}})

    # Ensure geospatial indexes exist
    stops_collection.create_index([("location", "2dsphere")])
    points_collection.create_index([("centroid", "2dsphere")])

    # Test OSRM connection
    def test_osrm_connection():
        try:
            test_url = f"{osrm_base_url}/route/v1/walking/9.098419837685748,45.48770571772714;9.101455965929162,45.489271335273926"
            response = requests.get(test_url, params={"overview": "false"})
            response.raise_for_status()
        except Exception:
            tqdm.write("Error connecting to OSRM server. Exiting.")
            exit()

    test_osrm_connection()

    # Function to calculate walking times using OSRM Table API with retry mechanism
    def calculate_walking_times(osrm_url, start_coords, end_coords_list, batch_size=75, retries=3, delay=60):
        all_durations = []
        for i in range(0, len(end_coords_list), batch_size):
            batch_coords = end_coords_list[i:i + batch_size]
            attempt = 0
            while attempt < retries:
                try:
                    coordinates = f"{start_coords[0]},{start_coords[1]}"
                    for end_coords in batch_coords:
                        coordinates += f";{end_coords[0]},{end_coords[1]}"
                    
                    url = f"{osrm_url}/table/v1/walking/{coordinates}"
                    response = requests.get(url)
                    response.raise_for_status()
                    data = response.json()
                    if "durations" in data and isinstance(data["durations"], list) and len(data["durations"]) > 0:
                        all_durations.extend(data["durations"][0][1:])
                    else:
                        all_durations.extend([None] * len(batch_coords))
                    break
                except requests.exceptions.RequestException:
                    attempt += 1
                    if attempt < retries:
                        time.sleep(delay)
                    else:
                        all_durations.extend([None] * len(batch_coords))
        return all_durations

    # Function to process a single point
    def process_point(point):
        point_id = point["_id"]
        point_coords = point.get("centroid", {}).get("coordinates")
        if not point_coords:
            return point_id, []  

        nearby_stops = stops_collection.find({
            "location": {
                "$nearSphere": {
                    "$geometry": {"type": "Point", "coordinates": point_coords},
                    "$maxDistance": max_walking_distance,
                }
            }
        })

        stop_ids = []
        stop_coords_list = []
        for stop in nearby_stops:
            stop_ids.append(stop.get("stop_id"))
            stop_coords_list.append(stop["location"]["coordinates"])

        if not stop_coords_list:
            return point_id, []  

        walking_times = calculate_walking_times(osrm_base_url, point_coords, stop_coords_list)
        if walking_times is None:
            return point_id, None  

        reachable_stops = []
        for stop_id, walking_time in zip(stop_ids, walking_times):
            if walking_time and walking_time <= max_walking_time:
                reachable_stops.append({"stop_id": stop_id, "walking_time": walking_time})

        return point_id, reachable_stops

    # Batch update points
    def batch_update_points(updates):
        if updates:
            points_collection.bulk_write([
                pymongo.UpdateOne({"_id": update[0]}, {"$set": {"reachable_stops": update[1]}})
                for update in updates
            ])

    total_points = points_collection.count_documents({})
    if total_points == 0:
        return

    points_cursor = points_collection.find().sort("_id")

    batch_updates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(
            executor.map(process_point, points_cursor),
            total=total_points,
            desc="Processing points"
        ))
        for result in results:
            point_id, reachable_stops = result
            batch_updates.append((point_id, reachable_stops))
            if len(batch_updates) >= 600:
                batch_update_points(batch_updates)
                batch_updates.clear()

    if batch_updates:
        batch_update_points(batch_updates)
    
    # 1) Remove points with empty reachable_stops
    result_points = points_collection.delete_many({"reachable_stops": {"$eq": []}})
    print(f"Removed {result_points.deleted_count} points with empty reachable_stops.")
    '''
    # 2) Remove stops not referenced by any point's reachable_stops
    distinct_stop_ids = points_collection.distinct("reachable_stops.stop_id")
    result_stops = stops_collection.delete_many({"stop_id": {"$nin": distinct_stop_ids}})
    print(f"Removed {result_stops.deleted_count} stops not referenced by any point's reachable_stops.")
    
    # 3) Remove edges that reference removed stops
    result_edges = edges_collection.delete_many({
        "$or": [
            {"stop_id": {"$nin": distinct_stop_ids}},
            {"next_stop_id": {"$nin": distinct_stop_ids}}
        ]
    })
    print(f"Removed {result_edges.deleted_count} edges that reference removed stops.")
    '''



###########################################################################################################################################


import os
import pymongo
import requests
from tqdm import tqdm
from geopy.distance import geodesic
import time
import logging
import concurrent.futures
from multiprocessing import cpu_count

# Configure logging without file logging or printing ERROR/CRITICAL messages to console
logger = logging.getLogger()
if logger.hasHandlers():
    logger.handlers.clear()

# Configure console handler to exclude ERROR and CRITICAL logs
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

# Filter to exclude ERROR and CRITICAL logs from console
class ExcludeErrorsFilter(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.ERROR

console_handler.addFilter(ExcludeErrorsFilter())
logger.addHandler(console_handler)

# Set the logging level for the logger
logger.setLevel(logging.INFO)

STOPS_COLLECTION = "stops"
POINTS_COLLECTION = "points"


MAX_WORKERS = cpu_count()

def process_stops(db, osrm_base_url, max_walking_distance_stop, max_walking_time):
    stops_collection = db[STOPS_COLLECTION]

    # Remove existing 'reachable_stops' from all stops
    stops_collection.update_many({}, {"$unset": {"reachable_stops": ""}})

    # Ensure geospatial index exists
    stops_collection.create_index([("location", "2dsphere")])

    # Test OSRM connection
    def test_osrm_connection():
        try:
            test_url = f"{osrm_base_url}/route/v1/walking/9.098419837685748,45.48770571772714;9.101455965929162,45.489271335273926"
            response = requests.get(test_url, params={"overview": "false"})
            response.raise_for_status()
        except Exception:
            tqdm.write("Error connecting to OSRM server. Exiting.")
            exit()

    test_osrm_connection()

    # Function to calculate distances using OSRM table API with retry mechanism
    def calculate_distances_table(start_coords, stop_coords_list, retries=3, delay=60):
        all_durations = []
        for i in range(0, len(stop_coords_list), 100):  # Adjust batch size to 100
            batch_coords = stop_coords_list[i:i + 100]
            attempt = 0
            while attempt < retries:
                try:
                    coords = f"{start_coords[0]},{start_coords[1]};" + ";".join(
                        [f"{stop[0]},{stop[1]}" for stop in batch_coords]
                    )
                    url = f"{osrm_base_url}/table/v1/walking/{coords}"
                    response = requests.get(url)
                    response.raise_for_status()
                    data = response.json()
                    if "durations" in data:
                        all_durations.extend(data["durations"][0][1:])
                    break
                except Exception:
                    attempt += 1
                    if attempt < retries:
                        time.sleep(delay)
                    else:
                        all_durations.extend([None] * len(batch_coords))
        return all_durations

    # Batch update stops
    def batch_update_stops(updates):
        if updates:
            stops_collection.bulk_write([
                pymongo.UpdateOne({"_id": update["_id"]}, {"$set": {"reachable_stops": update["reachable_stops"]}})
                for update in updates
            ])

    # Process a single stop
    def process_stop(stop):
        stop_id = stop["_id"]
        stop_coords = stop.get("location", {}).get("coordinates")
        if not stop_coords:
            return {"_id": stop_id, "reachable_stops": []}

        nearby_stops = list(stops_collection.aggregate([
            {
                "$geoNear": {
                    "near": {"type": "Point", "coordinates": stop_coords},
                    "distanceField": "distance",
                    "maxDistance": max_walking_distance_stop,
                    "spherical": True
                }
            },
            {
                "$project": {"stop_id": 1, "location.coordinates": 1, "distance": 1}
            }
        ]))

        stop_coords_list = [stop["location"]["coordinates"] for stop in nearby_stops if stop["_id"] != stop_id]
        stop_durations = calculate_distances_table(stop_coords, stop_coords_list)

        reachable_stops = []
        for stop, duration in zip(nearby_stops, stop_durations):
            if stop["_id"] != stop_id and duration and duration <= max_walking_time:
                reachable_stops.append({"stop_id": stop.get("stop_id"), "walking_time": duration})

        return {"_id": stop_id, "reachable_stops": reachable_stops}

    total_stops = stops_collection.count_documents({})
    if total_stops == 0:
        return

    stops_cursor = stops_collection.find({}, {"_id": 1, "location": 1}).sort("_id")

    batch_updates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(
            executor.map(process_stop, stops_cursor),
            total=total_stops,
            desc="Processing stops"
        ))
        for result in results:
            batch_updates.append(result)
            if len(batch_updates) >= 100:
                batch_update_stops(batch_updates)
                batch_updates.clear()

    if batch_updates:
        batch_update_stops(batch_updates)





###########################################################################################################################################



import geopandas as gpd
from pymongo import MongoClient
from tqdm import tqdm

def process_grid_population(db, grid_shapefile_path, population_attribute):
    """
    Processes a grid shapefile and saves population data to MongoDB.

    Args:
        db (pymongo.database.Database): MongoDB database connection.
        grid_shapefile_path (str): Path to the grid shapefile.
        population_attribute (str): The column name for the population data in the shapefile.
    """
    grid_population_collection = db["population"]  # Collection for storing grid population data

    # Remove the collection if it exists
    if "population" in db.list_collection_names():
        grid_population_collection.drop()
        print("Existing collection 'population' removed.")

    print("Loading grid shapefile...")
    # Load the grid shapefile
    grid = gpd.read_file(grid_shapefile_path)

    # Ensure the grid is in WGS84 CRS
    if grid.crs != "EPSG:4326":
        grid = grid.to_crs(epsg=4326)

    print("Saving grid population data to MongoDB...")
    batch_size = 1000
    for i in tqdm(range(0, len(grid), batch_size), desc="Saving Grids"):
        batch = grid.iloc[i:i + batch_size]
        records = []
        for _, row in batch.iterrows():
            # Extract geometry and population attribute
            geometry = row.geometry
            population = row.get(population_attribute, 0)  # Default to 0 if attribute is missing

            # Create the MongoDB document
            record = {
                "geometry": {"type": "Polygon", "coordinates": [list(geometry.exterior.coords)]},
                "POP": population
            }
            records.append(record)

        # Insert batch into MongoDB
        grid_population_collection.insert_many(records)

    print("All grid population data saved to MongoDB.")




###########################################################################################################################################
import multiprocessing
from pymongo import MongoClient, UpdateOne
from shapely.geometry import shape
from tqdm import tqdm

# Global variable to store the population collection in worker processes.
GRID_POPULATION_COLLECTION = None

def init_worker_mongo(uri, db_name):
    """
    Worker initializer that creates its own MongoClient using the provided URI and
    sets up the global GRID_POPULATION_COLLECTION.
    """
    global GRID_POPULATION_COLLECTION
    client = MongoClient(uri)
    GRID_POPULATION_COLLECTION = client[db_name]["population"]

def calculate_hexagon_population_mongo(hexagon_tuple):
    """
    For a given hexagon (passed as a tuple: (hex_id, hexagon geometry)),
    use MongoDB's $geoIntersects operator (with a projection) to retrieve only
    the intersecting population grid documents, then compute the proportional
    population contribution.
    """
    hex_id, hexagon = hexagon_tuple
    hexagon_geojson = hexagon.__geo_interface__
    
    try:
        intersecting_grids = list(GRID_POPULATION_COLLECTION.find({
            "geometry": {"$geoIntersects": {"$geometry": hexagon_geojson}}
        }, {"geometry": 1, "POP": 1}))
    except Exception as e:
        print(f"Error in geo query for hexagon {hex_id}: {e}")
        return (hex_id, 0)
    
    total_population = 0
    for grid in intersecting_grids:
        grid_geom = shape(grid["geometry"])
        intersection = hexagon.intersection(grid_geom)
        if not intersection.is_empty and grid_geom.area > 0:
            proportion = intersection.area / grid_geom.area
            total_population += proportion * grid.get("POP", 0)
            
    return (hex_id, total_population)

def process_population_computation(db):
    """
    Compute the population for each hexagon document in the points collection.
    For each hexagon, we use MongoDB's $geoIntersects operator to quickly retrieve
    only the population grids that intersect the hexagon, then use Shapely to compute
    intersection areas. The computed population is then updated via bulk operations.
    
    This function is called by passing just the db object.
    """
    points_collection = db["points"]

    print("Clearing old population data...")
    result = points_collection.update_many({}, {"$unset": {"population": ""}})
    print(f"Documents updated: {result.modified_count}")

    print("Fetching hexagon data...")
    hexagon_data = []
    for doc in points_collection.find({}, {"_id": 1, "hexagon": 1}):
        hexagon_data.append((doc["_id"], shape(doc["hexagon"])))

    # Extract connection details from the provided db.
    host, port = db.client.address
    uri = f"mongodb://{host}:{port}/"
    db_name = db.name

    print("Calculating population for each hexagon using $geoIntersects query...")
    # Use 75% of the available CPUs (at least 1)
    num_workers = max(1, int(multiprocessing.cpu_count() * 0.75))
    # Using a chunksize (e.g., 100) helps reduce overhead.
    with multiprocessing.Pool(processes=num_workers,
                              initializer=init_worker_mongo,
                              initargs=(uri, db_name)) as pool:
        results = list(tqdm(pool.imap(calculate_hexagon_population_mongo,
                                      hexagon_data, chunksize=100),
                            total=len(hexagon_data),
                            desc="Processing Hexagons"))

    bulk_updates = [
        UpdateOne({"_id": hex_id}, {"$set": {"population": int(population)}})
        for hex_id, population in results
    ]

    if bulk_updates:
        print("Updating database with computed population values...")
        points_collection.bulk_write(bulk_updates)

    print("Removing hexagons with population less than 100...")
    delete_result = points_collection.delete_many({"population": {"$lt": 100}})
    print(f"Deleted {delete_result.deleted_count} hexagons with low population.")
    print("Population calculation completed.")



###########################################################################################################################################

import os
import json
from pymongo import MongoClient

def create_stop_ids(db, city, output_dir="matrices"):
    """
    Creates a mapping from stop_id to index using the 'stops' collection in MongoDB,
    and saves the mapping to a JSON file.

    Args:
        db (pymongo.database.Database): MongoDB database connection.
        city (str): City name used in the output file name.
        output_dir (str): Directory where the JSON file will be saved.
    """
    stops_collection = db["stops"]
    
    # Fetch all stops with their stop_id field.
    stops = list(stops_collection.find({}, {"stop_id": 1}))
    
    # Create a mapping from stop_id to a unique index.
    stop_ids = {}
    for idx, stop in enumerate(stops):
        # Assumes each stop document has a unique "stop_id" field.
        stop_ids[stop["stop_id"]] = idx

    # Ensure the output directory exists.
    os.makedirs(output_dir, exist_ok=True)
    
    # Define the output file path.
    output_file = os.path.join(output_dir, f"stop_ids_{city}.json")
    
    # Save the mapping as a JSON file.
    with open(output_file, "w") as f:
        json.dump(stop_ids, f)
    
    print(f"Created mapping for {len(stop_ids)} stops and saved to '{output_file}'")

if __name__ == "__main__":
    # Connect to MongoDB (adjust connection string as needed)
    client = MongoClient("mongodb://localhost:27017")
    db = client["your_database_name"]  # replace with your database name
    city = "YourCity"  # replace with your city name

    create_stop_ids(db, city)



###########################################################################################################################################

import logging
import geopandas as gpd
import osmnx as ox
import pandas as pd
from pymongo import MongoClient

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Define groups and their associated tag filters.
groups = {
    "Healthcare": [
        {"key": "amenity", "value": "hospital"},
        {"key": "amenity", "value": "pharmacy"},
        {"key": "amenity", "value": "clinic"}
    ],
    "Education": [
        {"key": "amenity", "value": "school"},
        {"key": "amenity", "value": "university"},
        {"key": "amenity", "value": "college"}
    ],
    "Food": [
        {"key": "amenity", "value": "restaurant"},
        {"key": "amenity", "value": "cafe"},
        {"key": "amenity", "value": "bar"}
    ],
    "Retail": [
        {"key": "shop", "value": "supermarket"},
        {"key": "shop", "value": "mall"}
    ],
    "Recreation": [
        {"key": "leisure", "value": "park"},
        {"key": "amenity", "value": "cinema"},
        {"key": "leisure", "value": "sports_centre"}
    ],
    "Public_Service": [
        {"key": "amenity", "value": "police"},
        {"key": "amenity", "value": "fire_station"},
        {"key": "amenity", "value": "post_office"},
        {"key": "aeroway", "value": "aerodrome"}
    ]
}

def fetch_pois_in_boundary(boundary_shp_path, groups):
    """
    Fetch POIs from OSM within a given boundary shapefile.
    For each group (and each tag), it retrieves features,
    converts any polygon or multipolygon to centroids, and returns a combined GeoDataFrame.

    Parameters:
        boundary_shp_path (str): Path to the boundary shapefile.
        groups (dict): Dictionary with group names as keys and lists of tag dicts as values.

    Returns:
        GeoDataFrame: A GeoDataFrame containing point geometries along with 'group' and 'poi_type' attributes.
    """
    logging.info(f"Reading boundary shapefile: {boundary_shp_path}")
    boundary_gdf = gpd.read_file(boundary_shp_path)
    if boundary_gdf.empty:
        raise ValueError("Boundary shapefile is empty or could not be read.")

    # Merge all geometries into a single boundary
    boundary_polygon = boundary_gdf.geometry.unary_union

    # Reproject to WGS84 if necessary
    if boundary_gdf.crs and not boundary_gdf.crs.is_geographic:
        logging.info("Reprojecting boundary to WGS84 (EPSG:4326) for OSM query...")
        boundary_gdf = boundary_gdf.to_crs(epsg=4326)
        boundary_polygon = boundary_gdf.geometry.unary_union

    results = []  # To store results for each query

    # Loop through each group and its associated tags.
    for group_name, tags_list in groups.items():
        for tag in tags_list:
            key = tag["key"]
            value = tag["value"]
            tag_dict = {key: value}
            logging.info(f"Querying OSM for {group_name} - {key}:{value}...")
            try:
                poi_gdf = ox.features_from_polygon(boundary_polygon, tags=tag_dict)
            except Exception as e:
                logging.error(f"Error querying OSM for {group_name} - {key}:{value}: {e}")
                continue

            # Filter out empty results
            if poi_gdf.empty:
                logging.info(f"No data found for {group_name} - {key}:{value}.")
                continue

            # Filter for point geometries
            points = poi_gdf[poi_gdf.geom_type == "Point"].copy()

            # Convert polygons and multipolygons to centroids
            polygon_mask = poi_gdf.geom_type.isin(["Polygon", "MultiPolygon"])
            polygons_as_points = poi_gdf.loc[polygon_mask].copy()
            if not polygons_as_points.empty:
                polygons_as_points.geometry = polygons_as_points.geometry.centroid

            # Combine points and converted centroids
            if points.empty and polygons_as_points.empty:
                logging.info(f"No point geometries for {group_name} - {key}:{value}; skipping.")
                continue

            poi_points = pd.concat([points, polygons_as_points])
            # Add metadata columns for group and specific POI type.
            poi_points["group"] = group_name
            poi_points["poi_type"] = value

            logging.info(f"Retrieved {len(poi_points)} point feature(s) for {group_name} - {key}:{value}.")
            results.append(poi_points)

    if results:
        combined_gdf = gpd.GeoDataFrame(pd.concat(results, ignore_index=True), crs=boundary_gdf.crs)
        logging.info(f"Total POIs retrieved: {len(combined_gdf)}")
        return combined_gdf
    else:
        logging.info("No POIs found for any groups.")
        return gpd.GeoDataFrame(columns=["group", "poi_type", "geometry"], crs=boundary_gdf.crs)

def store_pois_to_mongodb(pois_gdf, db):
    """
    Stores POI points from a GeoDataFrame into MongoDB.
    The geometry is stored as a GeoJSON-like dict along with properties.

    Parameters:
        pois_gdf (GeoDataFrame): GeoDataFrame containing the POI data.
        db (MongoClient.Database): MongoDB database connection.
    """
    poi_collection = db["POI"]

    if pois_gdf.crs is None:
        raise ValueError("GeoDataFrame must have a valid CRS before storing to MongoDB.")

    records = []
    for idx, row in pois_gdf.iterrows():
        geom_dict = row.geometry.__geo_interface__
        doc = {
            "geometry": geom_dict,
            "properties": {
                "group": row.get("group", None),
                "poi_type": row.get("poi_type", None),
                "name": row.get("name", None)
            }
        }
        records.append(doc)

    if records:
        logging.info(f"Inserting {len(records)} POI documents into MongoDB...")
        result = poi_collection.insert_many(records)
        logging.info(f"Successfully inserted {len(result.inserted_ids)} POI documents.")
    else:
        logging.info("No POI records to insert.")





###########################################################################################################################################

import geopandas as gpd
from pymongo import MongoClient
import logging

def store_poi_geometry_to_mongodb(db, shapefile_path):
    """
    Stores only geometry from a POI shapefile into the MongoDB database.

    Args:
        db (pymongo.database.Database): MongoDB database connection.
        shapefile_path (str): Path to the POI shapefile.
    """

    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    poi_collection = db['POI']

    try:
        # Read the shapefile
        logging.info(f"Reading shapefile: {shapefile_path}")
        gdf = gpd.read_file(shapefile_path)

        # Ensure the GeoDataFrame has a CRS
        if gdf.crs is None:
            raise ValueError("Shapefile must have a valid coordinate reference system (CRS).")

        # Convert geometry to GeoJSON-like dictionary
        geometry_data = [{"geometry": geom.__geo_interface__} for geom in gdf["geometry"]]

        # Insert geometry data into MongoDB
        logging.info(f"Inserting {len(geometry_data)} POI geometries into the database...")
        result = poi_collection.insert_many(geometry_data)
        logging.info(f"Successfully inserted {len(result.inserted_ids)} POI geometries into the MongoDB database.")

    except Exception as e:
        logging.error(f"An error occurred: {e}")





###########################################################################################################################################


import os
import pymongo
import requests
from tqdm import tqdm
from multiprocessing import cpu_count
import time
import concurrent.futures

def process_poi_reachable_stops(db, osrm_base_url, max_walking_distance, max_walking_time):
    """
    Processes POI data to compute reachable stops using OSRM API and updates MongoDB.

    Args:
        db (pymongo.database.Database): MongoDB database connection.
        osrm_base_url (str): Base URL of the OSRM server.
        max_walking_distance (float): Maximum walking distance in meters.
        max_walking_time (float): Maximum walking time in seconds.
    """

    MAX_WORKERS = cpu_count()

    stops_collection = db["stops"]
    poi_collection = db["POI"]

    # Remove existing 'reachable_stops' from all points
    poi_collection.update_many({}, {"$unset": {"reachable_stops": ""}})

    # Ensure geospatial indexes exist
    stops_collection.create_index([("location", "2dsphere")])
    poi_collection.create_index([("geometry", "2dsphere")])

    # Test OSRM connection
    def test_osrm_connection():
        try:
            test_url = f"{osrm_base_url}/route/v1/walking/9.098419837685748,45.48770571772714;9.101455965929162,45.489271335273926"
            response = requests.get(test_url, params={"overview": "false"})
            response.raise_for_status()
        except Exception:
            tqdm.write("Error connecting to OSRM server. Exiting.")
            exit()

    test_osrm_connection()

    # Function to calculate walking times using OSRM Table API with retry mechanism
    def calculate_walking_times(start_coords, end_coords_list, batch_size=75, retries=3, delay=60):
        all_durations = []
        for i in range(0, len(end_coords_list), batch_size):
            batch_coords = end_coords_list[i:i + batch_size]
            attempt = 0
            while attempt < retries:
                try:
                    coordinates = f"{start_coords[0]},{start_coords[1]}"
                    for end_coords in batch_coords:
                        coordinates += f";{end_coords[0]},{end_coords[1]}"

                    url = f"{osrm_base_url}/table/v1/walking/{coordinates}"
                    response = requests.get(url)
                    response.raise_for_status()
                    data = response.json()
                    if "durations" in data and isinstance(data["durations"], list) and len(data["durations"]) > 0:
                        all_durations.extend(data["durations"][0][1:])  # Skip the first element (self-distance)
                    else:
                        all_durations.extend([None] * len(batch_coords))
                    break
                except requests.exceptions.RequestException:
                    attempt += 1
                    if attempt < retries:
                        time.sleep(delay)
                    else:
                        all_durations.extend([None] * len(batch_coords))
        return all_durations

    # Function to process a single point
    def process_point(point):
        point_id = point["_id"]
        point_coords = point.get("geometry", {}).get("coordinates")
        if not point_coords:
            return point_id, []  # Skip points without valid coordinates

        # Query stops near the point
        nearby_stops = stops_collection.find({
            "location": {
                "$nearSphere": {
                    "$geometry": {"type": "Point", "coordinates": point_coords},
                    "$maxDistance": max_walking_distance,
                }
            }
        })

        stop_ids = []
        stop_coords_list = []
        for stop in nearby_stops:
            stop_ids.append(stop.get("stop_id"))
            stop_coords_list.append(stop["location"]["coordinates"])

        if not stop_coords_list:
            return point_id, []  # No nearby stops found

        # Calculate walking times using the Table API
        walking_times = calculate_walking_times(point_coords, stop_coords_list)
        if walking_times is None:
            return point_id, None  # Skip this point if walking times couldn't be calculated

        reachable_stops = []
        for stop_id, walking_time in zip(stop_ids, walking_times):
            if walking_time and walking_time <= max_walking_time:
                reachable_stops.append({"stop_id": stop_id, "walking_time": walking_time})

        return point_id, reachable_stops

    # Batch update points
    def batch_update_points(updates):
        if updates:
            poi_collection.bulk_write([
                pymongo.UpdateOne({"_id": update[0]}, {"$set": {"reachable_stops": update[1]}})
                for update in updates
            ])

    # Main processing function
    total_points = poi_collection.count_documents({})
    if total_points == 0:
        print("No points found in the database. Exiting.")
        return

    print(f"Starting the processing of {total_points} points...")

    points_cursor = poi_collection.find().sort("_id")

    batch_updates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(
            executor.map(process_point, points_cursor),
            total=total_points,
            desc="Processing points"
        ))
        for result in results:
            point_id, reachable_stops = result
            batch_updates.append((point_id, reachable_stops))
            if len(batch_updates) >= 600:
                batch_update_points(batch_updates)
                batch_updates.clear()

    # Final batch update
    if batch_updates:
        batch_update_points(batch_updates)

    print("Processing of points is complete.")



###########################################################################################################################################


import logging
from pymongo import MongoClient

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def remove_unreachable_pois(db):
    """
    Remove POI documents that have neither reachable stops nor reachable points.
    A document is removed only if both fields are either missing or empty.
    
    Parameters:
        db (pymongo.database.Database): The MongoDB database connection.
    """
    poi_collection = db["POI"]
    
    # Build the query: both reachable_stops and reachable_points must be empty/missing.
    query = {
        "$and": [
            {"$or": [
                {"reachable_stops": {"$exists": False}},
                {"reachable_stops": {"$size": 0}}
            ]},
            {"$or": [
                {"reachable_points": {"$exists": False}},
                {"reachable_points": {"$size": 0}}
            ]}
        ]
    }
    
    # Perform the deletion.
    result = poi_collection.delete_many(query)
    logging.info(f"Removed {result.deleted_count} POI documents with no reachable stops AND no reachable points.")




###########################################################################################################################################



import os
import pymongo
import requests
from tqdm import tqdm
from multiprocessing import cpu_count
import time
import concurrent.futures

def process_poi_reachable_points(db, osrm_base_url, max_walking_distance, max_walking_time):
    """
    Processes POI data to compute reachable points using OSRM API and updates MongoDB.

    Args:
        db (pymongo.database.Database): MongoDB database connection.
        osrm_base_url (str): Base URL of the OSRM server.
        max_walking_distance (float): Maximum walking distance in meters.
        max_walking_time (float): Maximum walking time in seconds.
    """

    MAX_WORKERS = cpu_count()

    points_collection = db["points"]
    poi_collection = db["POI"]

    # Ensure geospatial indexes exist
    points_collection.create_index([("centroid", "2dsphere")])
    poi_collection.create_index([("geometry", "2dsphere")])

    # Remove existing 'reachable_points' from all POI
    poi_collection.update_many({}, {"$unset": {"reachable_points": ""}})

    # Test OSRM connection
    def test_osrm_connection():
        try:
            test_url = f"{osrm_base_url}/route/v1/walking/9.098419837685748,45.48770571772714;9.101455965929162,45.489271335273926"
            response = requests.get(test_url, params={"overview": "false"})
            response.raise_for_status()
        except Exception:
            tqdm.write("Error connecting to OSRM server. Exiting.")
            exit()

    test_osrm_connection()

    # Function to calculate walking times using OSRM Table API with retry mechanism
    def calculate_walking_times(start_coords, end_coords_list, batch_size=75, retries=3, delay=60):
        all_durations = []
        for i in range(0, len(end_coords_list), batch_size):
            batch_coords = end_coords_list[i:i + batch_size]
            attempt = 0
            while attempt < retries:
                try:
                    coordinates = f"{start_coords[0]},{start_coords[1]}"
                    for end_coords in batch_coords:
                        coordinates += f";{end_coords[0]},{end_coords[1]}"

                    url = f"{osrm_base_url}/table/v1/walking/{coordinates}"
                    response = requests.get(url)
                    response.raise_for_status()
                    data = response.json()

                    if "durations" in data and isinstance(data["durations"], list) and len(data["durations"]) > 0:
                        all_durations.extend(data["durations"][0][1:])  # Skip self-distance
                    else:
                        all_durations.extend([None] * len(batch_coords))
                    break
                except requests.exceptions.RequestException:
                    attempt += 1
                    if attempt < retries:
                        time.sleep(delay)
                    else:
                        all_durations.extend([None] * len(batch_coords))
        return all_durations

    # Function to process a single POI
    def process_poi(poi):
        poi_id = poi["_id"]
        poi_coords = poi.get("geometry", {}).get("coordinates")
        if not poi_coords:
            return poi_id, []  # Skip POIs with invalid coordinates

        # Query points near the POI
        nearby_points_cursor = points_collection.find({
            "centroid": {
                "$nearSphere": {
                    "$geometry": {"type": "Point", "coordinates": poi_coords},
                    "$maxDistance": max_walking_distance,
                }
            }
        })

        point_ids = []
        point_coords_list = []

        for pt in nearby_points_cursor:
            point_ids.append(pt["_id"])
            if pt.get("centroid") and pt["centroid"].get("coordinates"):
                point_coords_list.append(pt["centroid"]["coordinates"])

        if not point_coords_list:
            return poi_id, []

        # Calculate walking times using OSRM
        walking_times = calculate_walking_times(poi_coords, point_coords_list)
        if walking_times is None:
            return poi_id, []

        # Build reachable points
        reachable_points = []
        for pid, wtime in zip(point_ids, walking_times):
            if wtime is not None and wtime <= max_walking_time:
                reachable_points.append({"point_id": pid, "walking_time": wtime})

        return poi_id, reachable_points

    # Batch update POI with reachable points
    def batch_update_pois(updates):
        if updates:
            poi_collection.bulk_write([
                pymongo.UpdateOne(
                    {"_id": poi_id},
                    {"$set": {"reachable_points": rpoints}}
                )
                for (poi_id, rpoints) in updates
            ])

    # Main processing function
    total_pois = poi_collection.count_documents({})
    if total_pois == 0:
        print("No POI found in the database. Exiting.")
        return

    print(f"Starting the processing of {total_pois} POIs...")

    pois_cursor = poi_collection.find().sort("_id")

    batch_updates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(
            executor.map(process_poi, pois_cursor),
            total=total_pois,
            desc="Processing POIs"
        ))
        for poi_id, reachable_points in results:
            batch_updates.append((poi_id, reachable_points))
            if len(batch_updates) >= 600:
                batch_update_pois(batch_updates)
                batch_updates.clear()

    # Final batch update
    if batch_updates:
        batch_update_pois(batch_updates)

    print("Processing of POIs is complete.")



###########################################################################################################################################


#P2POI

import os
import numpy as np
import json
from math import inf
from tqdm import tqdm
from pymongo import MongoClient, UpdateOne

# --- Import Numba and set up types ---
from numba import njit, prange, types
from numba.typed import List

# Define tuple types for the transit graph and heap elements:
edge_tuple_type = types.Tuple((types.int64, types.float64, types.float64, types.float64))
heap_elem_multi_type = types.Tuple((types.float64, types.int64, types.float64, types.int64))

@njit(nogil=True)
def heap_push_multi(heap, item):
    heap.append(item)
    i = len(heap) - 1
    while i > 0:
        parent = (i - 1) // 2
        if heap[parent][0] > heap[i][0]:
            temp = heap[parent]
            heap[parent] = heap[i]
            heap[i] = temp
            i = parent
        else:
            break

@njit(nogil=True)
def heap_pop_multi(heap):
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
def multi_source_dijkstra(graph, initial_heap, num_nodes, num_sources):
    """
    Multi-source Dijkstra implemented in Numba.
    graph: A Numba typed list of lists with tuples: (neighbor, travel_time, edge_start, edge_end)
    initial_heap: A Numba typed list of heap elements: (cost, node, clock_time, source_index)
    """
    dist = np.full((num_sources, num_nodes), np.inf)
    visited_time = np.full((num_sources, num_nodes), np.inf)
    # Initialize distances from initial heap entries.
    for i in range(len(initial_heap)):
        cost, node, clock_time, source = initial_heap[i]
        if cost < dist[source, node]:
            dist[source, node] = cost

    heap = initial_heap
    while len(heap) > 0:
        current_cost, node, clock_time, source = heap_pop_multi(heap)
        if clock_time >= visited_time[source, node]:
            continue
        visited_time[source, node] = clock_time
        if current_cost > dist[source, node]:
            continue

        current_neighbors = graph[node]
        for j in range(len(current_neighbors)):
            neighbor, travel_time, edge_start, edge_end = current_neighbors[j]
            if edge_start < 0:
                arrival_time = clock_time + travel_time
                new_cost = current_cost + travel_time
            else:
                if edge_start >= clock_time:
                    waiting = edge_start - clock_time
                    arrival_time = edge_end
                    new_cost = current_cost + waiting + travel_time
                else:
                    continue
            if new_cost < dist[source, neighbor]:
                dist[source, neighbor] = new_cost
                heap_push_multi(heap, (new_cost, neighbor, arrival_time, source))
    return dist

@njit(parallel=True, nogil=True)
def compute_best_times(dist_matrix, direct_walk_array, reachable_stops_array):
    """
    For each source (row in dist_matrix), compute the minimum travel time by taking the minimum between:
      - direct_walk_array (precomputed direct walking times)
      - indirect travel: dist_matrix[source, stop_idx] + walking_time (for each reachable stop)
    reachable_stops_array is expected to be of shape (n, 2): [stop index, walking time]
    """
    num_sources = dist_matrix.shape[0]
    best_times = np.full(num_sources, np.inf)
    for i in prange(num_sources):
        best = direct_walk_array[i]
        for j in range(reachable_stops_array.shape[0]):
            s_idx = int(reachable_stops_array[j, 0])
            walk_time = reachable_stops_array[j, 1]
            alt = dist_matrix[i, s_idx] + walk_time
            if alt < best:
                best = alt
        best_times[i] = best
    return best_times

def load_data_from_mongodb(db, city, transit_graph_dir="matrices"):
    with open(f"{transit_graph_dir}/stop_ids_{city}.json", "r") as f:
        stop_id_to_index = json.load(f)
    stops = list(db["stops"].find({}, {"stop_id": 1, "reachable_stops": 1}))
    edges = list(db["edges"].find({}, {"stop_id": 1, "next_stop_id": 1, "travel_time": 1, "start_time": 1, "end_time": 1}))
    points = list(db["points"].find({}, {"_id": 1, "reachable_stops": 1}))
    pois = list(db["POI"].find({}, {"reachable_points.point_id": 1, "reachable_points.walking_time": 1, "reachable_stops": 1}))
    return stop_id_to_index, stops, edges, points, pois

def build_transit_graph(stop_id_to_index, stops, edges):
    from numba.typed import List as NumbaList
    transit_graph = NumbaList()
    num_stops = len(stop_id_to_index)
    # Create a list for each stop.
    for _ in range(num_stops):
        transit_graph.append(NumbaList.empty_list(edge_tuple_type))
    # Add walking edges from stops.
    for stop in stops:
        s_id = stop["stop_id"]
        if s_id in stop_id_to_index:
            s_idx = stop_id_to_index[s_id]
            for reachable in stop.get("reachable_stops", []):
                neighbor_id = reachable["stop_id"]
                if neighbor_id in stop_id_to_index:
                    neighbor_idx = stop_id_to_index[neighbor_id]
                    transit_graph[s_idx].append((neighbor_idx, float(reachable["walking_time"]), -1.0, -1.0))
    # Add scheduled transit edges.
    for edge in edges:
        s_idx = stop_id_to_index.get(edge["stop_id"])
        e_idx = stop_id_to_index.get(edge["next_stop_id"])
        if s_idx is not None and e_idx is not None:
            transit_graph[s_idx].append((e_idx, float(edge["travel_time"]), float(edge["start_time"]), float(edge["end_time"])))
    return transit_graph, len(stop_id_to_index)

# ──────────────────────────────────────────────────────────────────────────────
#  Shared mini‑helper: pull data & build the transit graph once
# ──────────────────────────────────────────────────────────────────────────────
def _prepare_data(db, city, transit_graph_dir):
    """
    Returns
    -------
    stop_id_to_index : dict[str,int]
    transit_graph    : numba.typed.Dict   (from build_transit_graph)
    num_stops        : int
    points           : list[dict]         (origin documents)
    pois             : list[dict]         (POI documents)
    """
    stop_id_to_index, stops, edges, points, pois = load_data_from_mongodb(
        db, city, transit_graph_dir
    )
    transit_graph, num_stops = build_transit_graph(stop_id_to_index, stops, edges)
    return stop_id_to_index, transit_graph, num_stops, points, pois


# ──────────────────────────────────────────────────────────────────────────────
#  1.  Fastest time to *any* POI
# ──────────────────────────────────────────────────────────────────────────────
def process_accessibility_time_to_pois(
    db,
    city,
    start_hours,
    max_travel_time=float("inf"),
    transit_graph_dir="matrices",
    group_size=10,
):
    """
    Compute the quickest travel time (min) from each origin point to *any*
    POI.  Writes one field per departure hour:

        Accessibility_P2POI_<hour>   – minutes or "Not reachable".
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pymongo import UpdateOne
    from tqdm import tqdm
    import numpy as np

    # ------------------------------------------------------------------
    # 1.  Prep data
    # ------------------------------------------------------------------
    (
        stop_id_to_index,
        transit_graph,
        num_stops,
        points,
        pois,
    ) = _prepare_data(db, city, transit_graph_dir)
    total_points = len(points)

    # POI lookup structures
    poi_direct_walk = {
        str(p["_id"]): {str(rp["point_id"]): float(rp["walking_time"]) for rp in p.get("reachable_points", [])}
        for p in pois
    }
    poi_reachable_stops = {
        str(p["_id"]): [
            (stop_id_to_index[rs["stop_id"]], float(rs["walking_time"]))
            for rs in p.get("reachable_stops", [])
            if rs["stop_id"] in stop_id_to_index
        ]
        for p in pois
    }
    reachable_stops_arr = np.array(
        [(s, w) for lst in poi_reachable_stops.values() for s, w in lst], dtype=np.float64
    )

    # ------------------------------------------------------------------
    # 2.  Per‑group worker (only fastest time)
    # ------------------------------------------------------------------
    def _time_worker(group_pts, T0, fld):
        init_heap = []  # (walk_t, stop_idx, abs_depart, src_idx)
        n_src = len(group_pts)
        for src_idx, doc in enumerate(group_pts):
            for rs in doc.get("reachable_stops", []):
                sid = rs["stop_id"]
                if sid in stop_id_to_index:
                    s_idx = stop_id_to_index[sid]
                    w = float(rs["walking_time"])
                    init_heap.append((w, s_idx, T0 + w, src_idx))

        dist = (
            multi_source_dijkstra(transit_graph, init_heap, num_stops, n_src)
            if init_heap
            else np.full((n_src, num_stops), np.inf)
        )

        direct_arr = np.array(
            [
                min(
                    (
                        direct.get(str(doc["_id"]), np.inf)
                        for direct in poi_direct_walk.values()
                    ),
                    default=np.inf,
                )
                for doc in group_pts
            ]
        )

        best_times = compute_best_times(dist, direct_arr, reachable_stops_arr)

        updates = []
        for i, doc in enumerate(group_pts):
            bt = best_times[i]
            val = "Not reachable" if bt == np.inf or bt > max_travel_time else bt / 60.0
            updates.append(UpdateOne({"_id": doc["_id"]}, {"$set": {fld: val}}))
        return updates

    # ------------------------------------------------------------------
    # 3.  Loop over hours
    # ------------------------------------------------------------------
    bulk_ops = []
    for hr in start_hours:
        T_dep = hr * 3600.0
        fld = f"Accessibility_P2POI_{hr}"
        futures = []
        with ThreadPoolExecutor() as ex:
            for s in range(0, total_points, group_size):
                grp = points[s : s + group_size]
                futures.append(ex.submit(_time_worker, grp, T_dep, fld))
            pbar = tqdm(total=len(futures), desc=f"Hour {hr} (time)")
            for fut in as_completed(futures):
                bulk_ops.extend(fut.result())
                pbar.update(1)
            pbar.close()

    if bulk_ops:
        db["points"].bulk_write(bulk_ops)
    print("Finished writing fastest‑time accessibility fields.")


# ──────────────────────────────────────────────────────────────────────────────
#  2.  Number of POIs reachable within `max_travel_time`
# ──────────────────────────────────────────────────────────────────────────────
def process_reachable_poi_counts(
    db,
    city,
    start_hours,
    max_travel_time,
    transit_graph_dir="matrices",
    group_size=10,
    count_field_prefix="ReachablePOIs_",
):
    """
    Compute *how many* POIs each point can reach within `max_travel_time`
    seconds for each departure hour.  Writes one integer field per hour:

        <count_field_prefix><hour>   (e.g. ReachablePOIs_8)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pymongo import UpdateOne
    from tqdm import tqdm
    import numpy as np

    # ------------------------------------------------------------------
    # 1.  Prep data
    # ------------------------------------------------------------------
    (
        stop_id_to_index,
        transit_graph,
        num_stops,
        points,
        pois,
    ) = _prepare_data(db, city, transit_graph_dir)
    total_points = len(points)

    poi_direct_walk = {
        str(p["_id"]): {str(rp["point_id"]): float(rp["walking_time"]) for rp in p.get("reachable_points", [])}
        for p in pois
    }
    poi_reachable_stops = {
        str(p["_id"]): [
            (stop_id_to_index[rs["stop_id"]], float(rs["walking_time"]))
            for rs in p.get("reachable_stops", [])
            if rs["stop_id"] in stop_id_to_index
        ]
        for p in pois
    }

    # Pre‑flatten once for Numba kernel
    reachable_stops_arr = np.array(
        [(s, w) for lst in poi_reachable_stops.values() for s, w in lst], dtype=np.float64
    )

    # ------------------------------------------------------------------
    # 2.  Per‑group worker (only counts)
    # ------------------------------------------------------------------
    def _count_worker(group_pts, T0, fld):
        init_heap = []
        n_src = len(group_pts)
        for src_idx, doc in enumerate(group_pts):
            for rs in doc.get("reachable_stops", []):
                sid = rs["stop_id"]
                if sid in stop_id_to_index:
                    s_idx = stop_id_to_index[sid]
                    w = float(rs["walking_time"])
                    init_heap.append((w, s_idx, T0 + w, src_idx))

        dist = (
            multi_source_dijkstra(transit_graph, init_heap, num_stops, n_src)
            if init_heap
            else np.full((n_src, num_stops), np.inf)
        )

        counts = np.zeros(n_src, dtype=np.int32)
        for i, doc in enumerate(group_pts):
            pt_id = str(doc["_id"])
            cnt = 0
            for poi_id, stop_list in poi_reachable_stops.items():
                best = poi_direct_walk.get(poi_id, {}).get(pt_id, np.inf)
                for s_idx, walk_poi in stop_list:
                    t = dist[i, s_idx] + walk_poi
                    if t < best:
                        best = t
                if best <= max_travel_time:
                    cnt += 1
            counts[i] = cnt

        return [
            UpdateOne({"_id": doc["_id"]}, {"$set": {fld: int(counts[idx])}})
            for idx, doc in enumerate(group_pts)
        ]

    # ------------------------------------------------------------------
    # 3.  Loop over hours
    # ------------------------------------------------------------------
    bulk_ops = []
    for hr in start_hours:
        T_dep = hr * 3600.0
        fld = f"{count_field_prefix}{hr}"
        futures = []
        with ThreadPoolExecutor() as ex:
            for s in range(0, total_points, group_size):
                grp = points[s : s + group_size]
                futures.append(ex.submit(_count_worker, grp, T_dep, fld))
            pbar = tqdm(total=len(futures), desc=f"Hour {hr} (counts)")
            for fut in as_completed(futures):
                bulk_ops.extend(fut.result())
                pbar.update(1)
            pbar.close()

    if bulk_ops:
        db["points"].bulk_write(bulk_ops)
    print("Finished writing reachable‑POI count fields.")




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
    "Accessibility_P2POI_{hour}", and the population data is included in the popup.
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
        field_name = f"Accessibility_P2POI_{hour}"
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
        field_name = f"Accessibility_P2POI_{hour}"
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
        field_name = f"Accessibility_P2POI_{hour}"
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


###############################################################################################################################################

def visualize_multi_hour_accessibility_Num_POI(
    points_collection,
    poi_collection,
    hours,
    html_file,
    thresholds,
    layer_opacity: float = 0.7,
    count_field_prefix: str = "ReachablePOIs_",
):
    """
    Build an interactive Folium map where each layer (one per `hour`)
    shows how many POIs each origin hexagon can reach within the pre‑set
    max‑travel‑time.  The map also includes:

      • a legend explaining the colour scale (bottom‑left)  
      • collapsible POI markers  
      • a bottom‑right table reporting the share of population that can
        reach at least X POIs (for each threshold provided).

    Parameters
    ----------
    points_collection : pymongo.collection.Collection
    poi_collection    : pymongo.collection.Collection
    hours             : list[int]   departure‑hour labels used in preprocessing
    html_file         : str         output path for the HTML file
    thresholds        : list[int]   POI‑count thresholds for the summary table
    layer_opacity     : float       polygon fill opacity
    count_field_prefix: str         DB field prefix (default “ReachablePOIs_”)
    """
    import folium
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib import colors as mcolors, ticker as mticker

    # --------------------------------------------------------
    # 1.  Collect all count values (across requested hours)
    # --------------------------------------------------------
    counts = []
    for hr in hours:
        fld = f"{count_field_prefix}{hr}"
        for doc in points_collection.find({}, {fld: 1}):
            v = doc.get(fld)
            if isinstance(v, (int, float)) and v >= 0:
                counts.append(int(v))
    if not counts:
        raise ValueError("No POI‑count data found in points_collection.")

    # Build “pretty” integer class breaks (≤ 10 classes)
    locator = mticker.MaxNLocator(integer=True, nbins=10)
    breaks = [int(b) for b in locator.tick_values(min(counts), max(counts))]
    if len(breaks) < 2:
        breaks = [min(counts), max(counts)]
    n_classes = len(breaks) - 1
    cmap = plt.cm.get_cmap("viridis", n_classes)
    class_cols = [mcolors.rgb2hex(cmap(i)[:3]) for i in range(cmap.N)]

    def colour_for(v):
        if not isinstance(v, (int, float)):
            return "#999999"
        for i in range(n_classes):
            lo, hi = breaks[i], breaks[i + 1]
            if (i < n_classes - 1 and lo <= v < hi) or (i == n_classes - 1 and lo <= v <= hi):
                return class_cols[i]
        return "#999999"

    # --------------------------------------------------------
    # 2.  Centre map (use POI bbox if present)
    # --------------------------------------------------------
    pois = list(poi_collection.find({}, {"geometry": 1}))
    if pois:
        lat = [p["geometry"]["coordinates"][1] for p in pois]
        lon = [p["geometry"]["coordinates"][0] for p in pois]
        center = [(min(lat) + max(lat)) / 2, (min(lon) + max(lon)) / 2]
    else:
        first_pt = points_collection.find_one({}, {"centroid": 1})
        if not first_pt:
            raise ValueError("No geometry found in points_collection.")
        center = list(first_pt["centroid"]["coordinates"])[::-1]

    m = folium.Map(location=center, zoom_start=12, tiles=None)
    folium.TileLayer(
        "https://tile.thunderforest.com/transport-dark/{z}/{x}/{y}.png?apikey=9594d1374f8440788cbeb2092dde1199",
        name="Thunderforest Transport",
        attr="Thunderforest",
        overlay=False,
    ).add_to(m)

    # Pre‑fetch static geometry once
    base_pts = list(points_collection.find({}, {"hexagon": 1, "population": 1}))

    # --------------------------------------------------------
    # 3.  One overlay layer per departure hour
    # --------------------------------------------------------
    for hr in hours:
        fld = f"{count_field_prefix}{hr}"
        docs_by_id = {str(d["_id"]): d for d in points_collection.find({}, {fld: 1, "population": 1})}
        fg = folium.FeatureGroup(name=f"{hr}h", overlay=True, control=True)
        for pt in base_pts:
            doc = docs_by_id.get(str(pt["_id"]))
            if not doc:
                continue
            count_val = doc.get(fld)
            if count_val is None:
                continue
            poly = folium.Polygon(
                [[lat, lon] for lon, lat in pt["hexagon"]["coordinates"][0]],
                color=None,
                fill=True,
                fill_color=colour_for(count_val),
                fill_opacity=layer_opacity,
                popup=f"Reachable POIs ({hr}h): {count_val}<br>Population: {doc.get('population', 'N/A')}",
            )
            poly.add_to(fg)
        fg.add_to(m)

    # --------------------------------------------------------
    # 4.  POI markers layer (collapsed by default)
    # --------------------------------------------------------
    poi_fg = folium.FeatureGroup(name="POIs", overlay=True, show=False)
    for p in pois:
        lon, lat = p["geometry"]["coordinates"]
        folium.Marker([lat, lon], icon=folium.Icon(color="red", icon="info-sign"), popup="POI").add_to(poi_fg)
    poi_fg.add_to(m)

    # --------------------------------------------------------
    # 5.  Legend (bottom‑left)
    # --------------------------------------------------------
    legend_html = (
        "<div style='position:fixed;bottom:50px;left:50px;width:260px;background:#fff;"
        "border:2px solid grey;border-radius:10px;padding:10px;z-index:1000;font-size:14px;'>"
        "<h4 style='margin:0 0 8px 0;'># of POIs reachable</h4>"
    )
    for i, col in enumerate(class_cols):
        rng = (
            f"{breaks[i]} – {breaks[i + 1] - 1}"
            if i < n_classes - 1
            else f"≥ {breaks[i]}"
        )
        legend_html += (
            "<div style='display:flex;align-items:center;margin-bottom:4px;'>"
            f"<div style='width:20px;height:20px;background:{col};border:1px solid black;margin-right:8px;'></div>"
            f"<span>{rng}</span></div>"
        )
    legend_html += "</div>"
    m.get_root().html.add_child(folium.Element(legend_html))

    # Layer control
    folium.LayerControl().add_to(m)

    # --------------------------------------------------------
    # 6.  Population summary table (bottom‑right)
    # --------------------------------------------------------
    def share_reaching(th):
        total_pop = good_pop = 0
        fields = {"population": 1, **{f"{count_field_prefix}{h}": 1 for h in hours}}
        for doc in points_collection.find({}, fields):
            pop = doc.get("population", 0)
            if not isinstance(pop, (int, float)) or pop <= 0:
                continue
            total_pop += pop
            if any(doc.get(f"{count_field_prefix}{h}", 0) >= th for h in hours):
                good_pop += pop
        return 0 if total_pop == 0 else 100 * good_pop / total_pop

    row_cells = "".join(
        f"<td style='border:1px solid #ddd;text-align:center;'>{int(share_reaching(th))}%</td>"
        for th in thresholds
    )
    header_cells = "".join(
        f"<th style='border:1px solid #ddd;text-align:center;'>{th}+</th>" for th in thresholds
    )
    table_html = (
        "<div style='position:fixed;bottom:50px;right:50px;width:240px;background:#f8f9fa;"
        "border:2px solid #ccc;border-radius:10px;padding:10px;z-index:1000;font-size:14px;"
        "box-shadow:2px 2px 8px rgba(0,0,0,0.3);'>"
        "<h4 style='margin:0 0 8px 0;text-align:center;background:#343a40;color:#fff;"
        "padding:6px;border-radius:5px;'>Population reaching ≥ X POIs (any hour)</h4>"
        "<table style='width:100%;border-collapse:collapse;font-family:Arial,sans-serif;'>"
        f"<tr><th style='border:1px solid #ddd;text-align:center;'>POIs</th>{header_cells}</tr>"
        f"<tr><td style='border:1px solid #ddd;text-align:center;'>Share</td>{row_cells}</tr>"
        "</table></div>"
    )
    m.get_root().html.add_child(folium.Element(table_html))

    # --------------------------------------------------------
    # 7.  Save map
    # --------------------------------------------------------
    m.save(html_file)
    print(f"Accessibility map saved to {html_file} with population stats at the bottom-right.")
