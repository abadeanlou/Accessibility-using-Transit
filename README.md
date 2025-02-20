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
   docker run -t -v "$(pwd):/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/Torino.osm.pbf
   docker run -t -v "$(pwd):/data" osrm/osrm-backend osrm-partition /data/Torino.osrm
   docker run -t -v "$(pwd):/data" osrm/osrm-backend osrm-customize /data/Torino.osrm
   docker run -t -i -p 5000:5000 -v "$(pwd):/data" osrm/osrm-backend osrm-routed --algorithm mld /data/Torino.osrm
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

This approach enables evaluating accessibility for various demographic groups and urban planning scenarios.

---

For any questions or contributions, feel free to open an issue or a pull request. 🚀
