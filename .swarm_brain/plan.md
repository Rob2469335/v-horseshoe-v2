# Plan: Image Monitor with Resize, Hash & SQLite

## Architecture Walkthrough

### Components
1. **Directory Watcher** - Uses `watchdog` or polling to detect new image files in target directory
2. **Image Resizer** - Uses Pillow to resize images > 1080p down to exactly 1080p (maintaining aspect ratio)
3. **MD5 Hash Calculator** - Computes MD5 hash of the original file before/after resize
4. **SQLite Metadata Store** - Stores filename, resolution, hash, timestamp, size in a SQLite database
5. **Error Handler** - Catches exceptions per image and logs them without stopping the monitor

### Flow
```
New Image Detected → Validate Extension → Resize if > 1080p → Compute MD5 → Insert to DB → Log Result
```

## Steps
1. Write architecture documentation to `.swarm_brain/architecture.md`
2. Write complete Python script to `image_monitor.py`
3. Write edge cases document to `.swarm_brain/edge_cases.md`
4. Return final response with all deliverables
