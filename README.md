# Accessibility-using-Transit
Computing Accessibility Using Transit

## Requirements
To run this project, ensure you have the following dependencies installed:

- **Python 3.8+**: Required for processing GTFS and accessibility computations.
- **MongoDB**: Stores GTFS data and accessibility results.
- **GeoPandas**: For spatial data processing.
- **OSRM**: Used for routing and calculating travel times.
- **Folium**: For visualization of accessibility data.
- **Dash**: For interactive web-based accessibility dashboards.
- **Numba**: Optimizes transit network calculations.
- **Matplotlib**: Used for visualizations.
- **NetworkX**: Handles transit graph processing.
- **OSMnx**: Retrieves OpenStreetMap data.

Install required Python packages using:

```bash
pip install -r requirements.txt
```

## OSRM Machine
To set up an OSRM routing machine for transit accessibility calculations:

1. **Download OSM Data**:
   - Obtain an `.osm.pbf` file for your region from [Geofabrik](https://download.geofabrik.de/).

2. **Run OSRM in Docker**:
   Execute the following commands in your terminal:

   ```bash
   docker run -t -v "$(pwd):/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/Milano.osm.pbf
   docker run -t -v "$(pwd):/data" osrm/osrm-backend osrm-partition /data/Milano.osrm
   docker run -t -v "$(pwd):/data" osrm/osrm-backend osrm-customize /data/Milano.osrm
   docker run -t -i -p 5000:5000 -v "$(pwd):/data" osrm/osrm-backend osrm-routed --algorithm mld /data/Milano.osrm
   ```

3. **Verify OSRM is Running**:
   - Open a browser and navigate to `http://localhost:5000/`.
   - You should see an OSRM JSON response.

## Population
Accessibility computations incorporate population data to assess transit reachability. The methodology includes:

- **Hexagonal Grid Analysis**: Population data is aggregated into hexagonal cells.
- **Reachability Metrics**: Computes transit accessibility scores for each hexagon.
- **GTFS Integration**: Uses GTFS data to determine transit service coverage.
- **Multi-Hour Analysis**: Accessibility is computed for different time intervals.
- **Precomputed Stop-to-Stop Matrices**: Uses A* algorithm for optimized routing.

This approach enables evaluating accessibility for various demographic groups and urban planning scenarios.

## Accessibility Computation
The project follows these key steps:

1. **Extract Data from GTFS**: Loads and stores GTFS data in MongoDB.
2. **Create Hexagonal Grid**: Generates a spatially indexed hexagonal grid.
3. **Compute Walking Time**: Determines walking times from stops and points using OSRM.
4. **Process POI Data**: Retrieves and processes POI locations from OpenStreetMap.
5. **Calculate Accessibility Metrics**:
   - **P2POI (Point-to-POI)**: Computes transit-based accessibility from grid points to POIs.
   - **POI2P (POI-to-Point)**: Computes accessibility from POIs to grid points.
   - **P2P (Point-to-Point)**: Evaluates direct transit-based connectivity.
6. **Generate Interactive Map**: Visualizes accessibility results using Folium and Dash.

For detailed execution, refer to `Accessibility_Calculation.ipynb` or run the scripts in sequence.

---

For any questions or contributions, feel free to open an issue or a pull request. 🚀
