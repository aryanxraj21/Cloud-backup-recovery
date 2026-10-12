"""Load test at LOW / MEDIUM / HIGH workload.

Mixed traffic: 60% list records, 15% get one record, 20% create record, 5% list backups.
Usage: python scripts/load_test.py [--base http://localhost:8000]
Writes results/load_test.json
"""
import argparse
import asyncio
import json
import random
import statistics
import time

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")
args = ap.parse_args()

LEVELS = [("low", 5, 500), ("medium", 25, 2000), ("high", 100, 5000)]  # (name, concurrent users, total requests)


async def one(client, H, max_id):
    r = random.random()
    t0 = time.perf_counter()
    if r < 0.60:
        resp = await client.get("/records", params={"limit": 20, "offset": random.randint(0, 900)}, headers=H)
    elif r < 0.75:
        resp = await client.get(f"/records/{random.randint(1, max_id)}", headers=H)
    elif r < 0.95:
        resp = await client.post("/records", json={"name": "load", "payload": {"v": random.random()}}, headers=H)
    else:
        resp = await client.get("/backups", headers=H)
    return (time.perf_counter() - t0) * 1000, resp.status_code


async def run_level(name, users, total, H):
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    async with httpx.AsyncClient(base_url=args.base, timeout=30, limits=limits) as client:
        sem = asyncio.Semaphore(users)
        lat, codes = [], {}

        async def task():
            async with sem:
                try:
                    ms, code = await one(client, H, 1000)
                except Exception:
                    ms, code = 30000, "error"
                lat.append(ms)
                codes[code] = codes.get(code, 0) + 1

        t0 = time.perf_counter()
        await asyncio.gather(*[task() for _ in range(total)])
        wall = time.perf_counter() - t0
    s = sorted(lat)
    ok = sum(v for k, v in codes.items() if isinstance(k, int) and k < 500)
    return {"level": name, "concurrent_users": users, "requests": total, "duration_s": round(wall, 2),
            "throughput_rps": round(total / wall, 1), "avg_ms": round(statistics.mean(s), 1),
            "p50_ms": round(s[len(s) // 2], 1), "p95_ms": round(s[int(len(s) * 0.95)], 1),
            "p99_ms": round(s[int(len(s) * 0.99)], 1), "max_ms": round(s[-1], 1),
            "success": ok, "errors": total - ok, "error_rate_pct": round(100 * (total - ok) / total, 2),
            "status_codes": {str(k): v for k, v in codes.items()}}


async def main():
    async with httpx.AsyncClient(base_url=args.base) as c:
        tok = (await c.post("/auth/login", json={"username": "operator", "password": "operator123"})).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    out = []
    for name, users, total in LEVELS:
        res = await run_level(name, users, total, H)
        print(json.dumps(res))
        out.append(res)
        await asyncio.sleep(1)
    json.dump(out, open("results/load_test.json", "w"), indent=2)


asyncio.run(main())
