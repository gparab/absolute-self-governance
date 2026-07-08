import asyncio
import time
import httpx
import numpy as np

async def send_request(client: httpx.AsyncClient, url: str, headers: dict) -> float:
    start = time.time()
    try:
        response = await client.post(
            url,
            json={"action": "opened", "issue": {"title": "Scale testing", "body": "Benchmarking pipeline"}},
            headers=headers,
            timeout=30.0
        )
        latency = time.time() - start
        return latency if response.status_code == 200 else -1.0
    except Exception:
        return -1.0

async def run_load_test(url: str, headers: dict, concurrent_requests: int = 10, total_requests: int = 50):
    print(f"Starting pipeline load test: sending {total_requests} total requests with concurrency of {concurrent_requests}...")
    
    async with httpx.AsyncClient() as client:
        latencies = []
        sem = asyncio.Semaphore(concurrent_requests)
        
        async def worker():
            async with sem:
                lat = await send_request(client, url, headers)
                latencies.append(lat)
                
        start_time = time.time()
        await asyncio.gather(*(worker() for _ in range(total_requests)))
        total_duration = time.time() - start_time
        
        # Filter successful requests (latency > 0)
        valid_latencies = [lat for lat in latencies if lat > 0.0]
        failures = latencies.count(-1.0)
        
        print("\n--- Load Test Results ---")
        print(f"Total Duration: {total_duration:.2f}s")
        print(f"Throughput: {len(valid_latencies) / total_duration:.2f} req/s")
        print(f"Success Rate: {len(valid_latencies) / total_requests * 100:.1f}% ({len(valid_latencies)} successes, {failures} failures)")
        
        if valid_latencies:
            p50 = np.percentile(valid_latencies, 50)
            p90 = np.percentile(valid_latencies, 90)
            p99 = np.percentile(valid_latencies, 99)
            print(f"p50 Latency: {p50:.4f}s")
            print(f"p90 Latency: {p90:.4f}s")
            print(f"p99 Latency: {p99:.4f}s")
        else:
            print("No successful requests recorded.")

if __name__ == "__main__":
    import sys
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/webhook"
    auth_headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": "sha256=mock_signature",
        "Authorization": "Bearer tenant_tenantA_key"
    }
    asyncio.run(run_load_test(target_url, auth_headers))
