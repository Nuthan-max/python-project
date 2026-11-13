import requests
import json

hosp = requests.get('http://127.0.0.1:5000/api/hospitals?lat=28.6315&lng=77.2167&limit=2').json()['items'][0]['id']
amb = requests.post('http://127.0.0.1:5000/api/ambulances', json={'lat': 28.63, 'lng': 77.21}).json()['ambulance']['id']
res = requests.post('http://127.0.0.1:5000/api/missions', json={
    'ambulance_id': amb,
    'hospital_id': hosp,
    'emergency_lat': 28.6315,
    'emergency_lng': 77.2167,
    'algorithm': 'astar',
    'auto_start': False
}).json()

print("COMPARISON RESPONSE:")
print(json.dumps(res.get('comparison', {}), indent=2))
