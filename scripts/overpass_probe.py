import time
import requests

Q = ('[out:json][timeout:60];'
     'node["amenity"="hospital"](around:2000,28.6304,77.2177);'
     'out center tags;')

t = time.time()
for url in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]:
    try:
        r = requests.post(url, data={"data": Q}, timeout=90,
                          headers={"User-Agent": "RescueRoute/1.0 (student project)"})
        print(url, "status", r.status_code, "in", round(time.time() - t, 1), "s")
        if r.ok:
            els = r.json().get("elements", [])
            print("elements:", len(els), [e.get("tags", {}).get("name") for e in els][:8])
            break
        print(r.text[:300])
    except Exception as exc:
        print(url, "ERR", type(exc).__name__, exc, round(time.time() - t, 1))
