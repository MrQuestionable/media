import os
import sys
import time
import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response
from s3_service import S3Service
import time
import asyncpg
from fastapi import Request

if sys.platform == "win32":
    opencv_bin_dir = r"C:\opencv\build\x64\vc16\bin"
    if os.path.exists(opencv_bin_dir):
        os.add_dll_directory(opencv_bin_dir)

import native_engine

app = FastAPI(title="High-Performance Polyglot Image Pipeline")
s3 = S3Service()
@app.get("/health")
def health_check():
    return {"status": "healthy", "engine": "C++ Native Engine Loaded"}

@app.post("/api/v1/process-edges")
async def process_edges(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image.")

    contents = await file.read()
    np_buffer = np.frombuffer(contents, dtype=np.uint8)
    image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)

    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image encoding.")

    start_time = time.perf_counter()
    edges = native_engine.apply_edge_detection(image)
    duration_ms = (time.perf_counter() - start_time) * 1000

    success, encoded_img = cv2.imencode(".png", edges)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to encode image.")

    return Response(
        content=encoded_img.tobytes(),
        media_type="image/png",
        headers={
            "X-Process-Latency-Ms": f"{duration_ms:.2f}",
            "X-Image-Resolution": f"{image.shape[1]}x{image.shape[0]}"
        }
    )

@app.on_event("startup")
async def startup_db_client():
    app.state.db_pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL")
    )
    async with app.state.db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS process_metrics (
                id SERIAL PRIMARY KEY,
                input_s3_key VARCHAR(255),
                output_s3_key VARCHAR(255),
                resolution VARCHAR(20),
                cpp_latency_ms NUMERIC(6, 2),
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

@app.on_event("shutdown")
async def shutdown_db_client():
    await app.state.db_pool.close()

@app.post("/api/v1/process-s3-image")
async def process_s3_image(s3_key: str, request: Request):
    try:
        raw_bytes = s3.download_image_to_memory(s3_key)
        np_buffer = np.frombuffer(raw_bytes, dtype=np.uint8)
        image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)

        if image is None:
            raise HTTPException(status_code=400, detail="Invalid image encoding from S3.")

        t0 = time.perf_counter()
        edges = native_engine.apply_edge_detection(image)
        duration_ms = (time.perf_counter() - t0) * 1000

        success, encoded_img = cv2.imencode(".png", edges)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to encode image.")

        output_key = f"processed/{s3_key}"
        presigned_url = s3.upload_processed_image(encoded_img.tobytes(), output_key)

        resolution = f"{image.shape[1]}x{image.shape[0]}"

        async with request.app.state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO process_metrics 
                (input_s3_key, output_s3_key, resolution, cpp_latency_ms) 
                VALUES ($1, $2, $3, $4)
            """, s3_key, output_key, resolution, round(duration_ms, 2))

        return {
            "status": "success",
            "input_s3_key": s3_key,
            "output_s3_key": output_key,
            "download_url": presigned_url,
            "cpp_latency_ms": round(duration_ms, 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/v1/metrics")
async def get_performance_metrics(request: Request):
    try:
        async with request.app.state.db_pool.acquire() as conn:
            metrics_row = await conn.fetchrow("""
                SELECT 
                    COUNT(*)::INT AS total_processed,
                    COALESCE(ROUND(AVG(cpp_latency_ms)::numeric, 2), 0.0) AS avg_latency_ms,
                    COALESCE(ROUND(MIN(cpp_latency_ms)::numeric, 2), 0.0) AS min_latency_ms,
                    COALESCE(ROUND(MAX(cpp_latency_ms)::numeric, 2), 0.0) AS max_latency_ms,
                    COALESCE(ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cpp_latency_ms)::numeric, 2), 0.0) AS p50_latency_ms,
                    COALESCE(ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cpp_latency_ms)::numeric, 2), 0.0) AS p95_latency_ms,
                    COALESCE(ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cpp_latency_ms)::numeric, 2), 0.0) AS p99_latency_ms
                FROM process_metrics;
            """)

            breakdown_rows = await conn.fetch("""
                SELECT 
                    resolution,
                    COUNT(*)::INT AS sample_count,
                    ROUND(AVG(cpp_latency_ms)::numeric, 2) AS avg_latency_ms,
                    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cpp_latency_ms)::numeric, 2) AS p95_latency_ms
                FROM process_metrics
                GROUP BY resolution
                ORDER BY sample_count DESC;
            """)

            return {
                "status": "success",
                "summary": {
                    "total_processed": metrics_row["total_processed"],
                    "latency_ms": {
                        "p50": float(metrics_row["p50_latency_ms"]),
                        "p95": float(metrics_row["p95_latency_ms"]),
                        "p99": float(metrics_row["p99_latency_ms"]),
                        "avg": float(metrics_row["avg_latency_ms"]),
                        "min": float(metrics_row["min_latency_ms"]),
                        "max": float(metrics_row["max_latency_ms"]),
                    }
                },
                "resolution_breakdown": [
                    {
                        "resolution": row["resolution"],
                        "samples": row["sample_count"],
                        "avg_latency_ms": float(row["avg_latency_ms"]),
                        "p95_latency_ms": float(row["p95_latency_ms"])
                    }
                    for row in breakdown_rows
                ]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))