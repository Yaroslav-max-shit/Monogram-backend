from fastapi import APIRouter, Response
import json
import os
from collections import Counter, defaultdict
import statistics

router = APIRouter(tags=["metrics"])

METRICS_FILE = "logs/metrics.jsonl"

@router.get("/metrics")
async def get_metrics():
    """Return Prometheus-style metrics"""
    if not os.path.exists(METRICS_FILE):
        return Response("# No metrics yet\n", media_type="text/plain")
    
    status_counts = Counter()
    path_counts = Counter()
    durations = []
    
    with open(METRICS_FILE) as f:
        for line in f:
            try:
                m = json.loads(line.strip())
                status_counts[m["status"]] += 1
                path_counts[m["path"]] += 1
                durations.append(m["duration_ms"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    
    output = []
    output.append("# HELP http_requests_total Total HTTP requests")
    output.append("# TYPE http_requests_total counter")
    for status, count in status_counts.most_common():
        output.append(f'http_requests_total{{status="{status}"}} {count}')
    
    output.append("")
    output.append("# HELP http_request_duration_ms Request duration in ms")
    output.append("# TYPE http_request_duration_ms gauge")
    if durations:
        output.append(f"http_request_duration_ms{{quantile=\"avg\"}} {statistics.mean(durations):.2f}")
        output.append(f"http_request_duration_ms{{quantile=\"p50\"}} {statistics.median(durations):.2f}")
        if len(durations) > 10:
            durations.sort()
            p99 = durations[int(len(durations) * 0.99)]
            output.append(f"http_request_duration_ms{{quantile=\"p99\"}} {p99:.2f}")
    
    return Response("\n".join(output), media_type="text/plain")

@router.get("/metrics/dashboard")
async def metrics_dashboard():
    """Simple HTML dashboard"""
    if not os.path.exists(METRICS_FILE):
        return {"error": "No metrics yet"}
    
    status_counts = Counter()
    path_counts = Counter()
    durations = []
    
    with open(METRICS_FILE) as f:
        for line in f:
            try:
                m = json.loads(line.strip())
                status_counts[m["status"]] += 1
                path_counts[m["path"]] += 1
                durations.append(m["duration_ms"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    
    return {
        "total_requests": sum(status_counts.values()),
        "status_codes": dict(status_counts.most_common()),
        "popular_paths": dict(path_counts.most_common(10)),
        "avg_duration_ms": round(statistics.mean(durations), 2) if durations else 0,
        "total_metrics": len(durations),
    }
