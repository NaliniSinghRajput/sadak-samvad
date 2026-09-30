"""Export edge-node detections (Road Runners bus-camera node) into the web evidence layer.

Inputs (produced by the edge node in edge/ during real drives):
  * naini-3_gps-*.geojson          - Varanasi night drive, 29 Aug 2026, per-event phone GNSS fix
  * morning_drive_detections_full.json - Bhubaneswar 4-camera drive (KIIT -> Esplanade), 19 Sep 2026
  * route_kiit_to_esplanade.json   - planned route geometry for that drive (GPS was off; position
                                     is interpolated along the route by elapsed time and flagged so)
Output: web/data/evidence.json
"""
import json, math, sys, collections, os

def load(p): return json.load(open(p))

def interp(points, cum, frac):
    L = cum[-1] * frac
    for i in range(1, len(cum)):
        if cum[i] >= L:
            a = (L - cum[i-1]) / max(1e-9, cum[i] - cum[i-1])
            (y0, x0), (y1, x1) = points[i-1], points[i]
            return [y0 + a*(y1-y0), x0 + a*(x1-x0)]
    return points[-1]

def main(gj, det, route, out):
    ev = []
    for f in load(gj)["features"]:
        p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
        ev.append(dict(id=p["event_id"], lat=round(lat,6), lon=round(lon,6), label=p["label"],
                       severity=p.get("severity"), conf=p.get("confidence"), city="Varanasi",
                       state="Uttar Pradesh", source="edge-camera", pos="phone GNSS",
                       drive="Naini-Varanasi night drive, 29 Aug 2026", vehicle=p.get("vehicle_id")))
    d = load(det); r = load(route)
    T = max(e["t"] for e in d["events"]) or 1
    counts = collections.Counter(e["category"] for e in d["events"])
    n = 0
    for e in d["events"]:
        if e["label"] not in ("pothole", "animal_in_carriageway"): continue
        n += 1
        lat, lon = interp(r["points"], r["cum"], e["t"]/T)
        ev.append(dict(id=f"TRR3-{n:04d}", lat=round(lat,6), lon=round(lon,6), label=e["label"],
                       severity=e.get("severity"), conf=e["confidence"], city="Bhubaneswar",
                       state="Odisha", source="edge-camera", camera=e["camera_name"]+" ("+e["position"]+")",
                       pos="interpolated on route by time (GPS off)",
                       drive="KIIT -> Esplanade 4-camera drive, 19 Sep 2026",
                       thumb=e.get("thumbnail_b64") if e["label"]=="pothole" else None))
    stats = dict(bhubaneswar_events=len(d["events"]), by_category=counts,
                 route_km=round(r["length_m"]/1000,1), drive_s=T)
    json.dump(dict(events=ev, stats=stats, route=[[round(a,5),round(b,5)] for a,b in r["points"]]),
              open(out,"w"), separators=(",",":"))
    print(len(ev), "evidence events ->", out)

if __name__ == "__main__":
    main(*sys.argv[1:5])
