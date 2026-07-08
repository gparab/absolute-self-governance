import os
import time
import tempfile
import threading
import yaml
from self_governance.nudger import ContinuousNudger
from self_governance.dimensioning import dimension_swarm

def test_dimensioning_perf_and_memory():
    print("--- Stress Testing Dimensioning Performance ---")
    import gc
    try:
        import psutil
        process = psutil.Process(os.getpid())
        def get_memory():
            return process.memory_info().rss / (1024 * 1024) # MB
    except ImportError:
        def get_memory():
            return 0.0

    gc.collect()
    mem_before = get_memory()
    start_time = time.time()
    
    # 10M Agents
    config = dimension_swarm([1000000.0], [[10.0]])
    duration = time.time() - start_time
    gc.collect()
    mem_after = get_memory()
    
    print(f"Dimensioned 10M agents in {duration:.4f}s.")
    if mem_before > 0:
        print(f"Memory overhead: {mem_after - mem_before:.2f} MB (Before: {mem_before:.2f} MB, After: {mem_after:.2f} MB)")
    
    assert len(config.swarm) == 10000000
    assert duration < 0.1, f"Expected dimensioning to take < 0.1s, took {duration:.4f}s"
    
    # Try 50M agents to verify scalability
    start_time = time.time()
    config_50m = dimension_swarm([5000000.0], [[10.0]])
    duration_50m = time.time() - start_time
    assert len(config_50m.swarm) == 50000000
    print(f"Dimensioned 50M agents in {duration_50m:.4f}s.")
    assert duration_50m < 0.1

def test_nudger_concurrency_stress():
    print("--- Stress Testing Nudger Concurrency and Race Conditions ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        handoff_path = os.path.join(tmp_dir, "handoff.md")
        log_path = os.path.join(tmp_dir, "roster_rotation_log.md")
        prompt_path = os.path.join(tmp_dir, "prompt_draft.md")
        
        nudger = ContinuousNudger(working_directory=tmp_dir)
        
        # Start the watcher in a daemon thread
        watcher_thread = threading.Thread(target=nudger.watch_handoff, daemon=True)
        watcher_thread.start()
        
        # Spin up multiple threads that concurrently write to handoff.md
        num_writers = 10
        writes_per_writer = 50
        errors = []
        
        def writer_func(writer_id):
            for i in range(writes_per_writer):
                try:
                    if i % 3 == 0:
                        content = f"status: IN_PROGRESS\niteration: {writer_id}_{i}\n"
                    elif i % 3 == 1:
                        content = ":::malformed yaml:::"
                    else:
                        content = f"status: COMPLETED\ncandidates:\n  - agent_{writer_id}_{i}\n"
                    
                    with open(handoff_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    
                    time.sleep(0.005)
                except Exception as e:
                    errors.append(e)
        
        writer_threads = []
        for i in range(num_writers):
            t = threading.Thread(target=writer_func, args=(i,))
            writer_threads.append(t)
            t.start()
            
        for t in writer_threads:
            t.join()
            
        time.sleep(0.5)
        
        # Write one final COMPLETED status
        final_candidates = ["final_agent_1", "final_agent_2"]
        final_content = yaml.dump({"status": "COMPLETED", "candidates": final_candidates})
        with open(handoff_path, "w", encoding="utf-8") as f:
            f.write(final_content)
            
        success = False
        start_wait = time.time()
        while time.time() - start_wait < 3.0:
            if os.path.exists(log_path) and os.path.exists(prompt_path):
                with open(log_path, "r", encoding="utf-8") as f:
                    log_text = f.read()
                if "final_agent_1" in log_text:
                    success = True
                    break
            time.sleep(0.1)
            
        assert watcher_thread.is_alive(), "Watcher thread crashed or stopped!"
        assert success, "Nudger failed to recover or process the final COMPLETED state!"

def test_webhook_concurrent_load():
    from fastapi.testclient import TestClient
    from self_governance.github_app import app
    from self_governance.db import SessionLocal, RateLimitEntry
    
    db = SessionLocal()
    db.query(RateLimitEntry).delete()
    db.commit()
    db.close()
    
    client = TestClient(app)
    
    results = []
    threads = []
    
    def worker():
        response = client.get(
            "/dashboard",
            headers={"Authorization": "Bearer tenant_tenantA_key"}
        )
        results.append(response.status_code)
        
    for _ in range(120):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    status_200 = results.count(200)
    status_429 = results.count(429)
    
    print(f"Concurrent stress test: {status_200} requests succeeded (200), {status_429} requests rate-limited (429)")
    
    assert status_200 == 100
    assert status_429 == 20
    
    db = SessionLocal()
    db.query(RateLimitEntry).delete()
    db.commit()
    db.close()
