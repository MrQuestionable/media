# Cloud-Native Polyglot Media Engine

**A high-throughput image processing service that pairs a zero-copy C++17/OpenCV compute core with an asynchronous FastAPI gateway, streams images between AWS S3 and RAM without touching disk, and records latency telemetry in PostgreSQL.**

[![CI](https://github.com/<USERNAME>/<REPO>/actions/workflows/ci.yml/badge.svg)](https://github.com/<USERNAME>/<REPO>/actions/workflows/ci.yml)
![C++17](https://img.shields.io/badge/C%2B%2B-17-00599C?logo=cplusplus&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-5C3EE8?logo=opencv&logoColor=white)
![AWS S3](https://img.shields.io/badge/AWS-S3%20%7C%20SigV4-FF9900?logo=amazons3&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-multi--stage-2496ED?logo=docker&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Engineering Checkpoints & System Design Decisions](#engineering-checkpoints--system-design-decisions)
4. [Complete Tech Stack](#complete-tech-stack)
5. [Repository Layout](#repository-layout)
6. [API Reference](#api-reference)
7. [Local Deployment & Quickstart](#local-deployment--quickstart)
8. [Verification & Observability](#verification--observability)
9. [License & Author](#license--author)

---

## Overview

Python is an excellent language for I/O orchestration and a poor one for per-pixel loops. C++ is the opposite. This project draws the boundary deliberately:

- **C++17 + OpenCV** implements the compute-heavy step (Canny edge detection) in a native extension module.
- **Python 3.11 + FastAPI** owns the network edge: request handling, S3 access, database telemetry and presigned URL issuance.
- **pybind11's buffer protocol** lets the two share image memory in-process, so the language boundary does not add a copy.
- **AWS S3** stores inputs and outputs in a private bucket. Image bytes move through RAM only, never through container disk.
- **PostgreSQL** records per-request latency, and percentile statistics are computed inside the database.
- **Docker Compose** and **GitHub Actions** make the whole system reproducible and continuously verified.

---

## Architecture

```
                         +------------------------------+
                         |            Client            |
                         |  (curl / browser / service)  |
                         +--------------+---------------+
                                        |  HTTPS
                                        v
 +--------------------------------------------------------------------------+
 |                    FastAPI Gateway  (Uvicorn, Python 3.11)               |
 |                                                                          |
 |   POST /api/v1/process-s3-image     POST /api/v1/process-edges           |
 |   GET  /api/v1/metrics              GET  /health                         |
 +------+-------------------------------------------------------+-----------+
        |                                                       |
        | (1) fetch object                                      | (5) async log
        v                                                       v
 +--------------+   bytes -> io.BytesIO (RAM only)     +------------------------+
 |   AWS S3     |------------------------------------> |  asyncpg conn. pool    |
 |  (private,   |                                      +-----------+------------+
 |  SigV4)      |                                                  |
 +------^-------+                                                  v
        |                                                +------------------+
        | (4) upload from RAM                            |  PostgreSQL 15   |
        |     + presigned URL (60 min)                   |  process_metrics |
        |                                                |  PERCENTILE_CONT |
 +------+-----------------------------------+            +------------------+
 |  In-memory encode (BytesIO / ndarray)    |
 +------^-----------------------------------+
        |
        | (3) result ndarray
        |
 +------+-----------------------------------+
 |   C++17 Engine  (native_engine.so)       |
 |   OpenCV core + imgproc  ->  Canny       |
 |   cv::Mat wraps the NumPy buffer         |
 +------^-----------------------------------+
        |
        | (2) zero-copy buffer protocol (pybind11)
        |
 +------+-----------------------------------+
 |   NumPy ndarray (decoded image, RAM)     |
 +------------------------------------------+
```

**Request flow (`/api/v1/process-s3-image`):**

1. The gateway streams the S3 object straight into an in-memory `io.BytesIO`.
2. The image is decoded in memory and handed to C++ through the buffer protocol. `cv::Mat` wraps the existing NumPy memory.
3. The C++ engine runs Canny edge detection and returns the result array.
4. The result is encoded in memory and uploaded back to S3. A 60-minute SigV4 presigned URL is generated for download.
5. Latency and metadata are written to PostgreSQL through the `asyncpg` pool without blocking the event loop.

---

## Engineering Checkpoints & System Design Decisions

### 1. Zero-Copy Buffer Protocol vs. Disk/IPC Serialization

**Decision:** The C++ engine is an in-process extension module that receives the NumPy array through pybind11's buffer protocol, and wraps that memory in a `cv::Mat` header without copying pixels.

**Why:** The common alternatives are to write temp files, spawn a subprocess, or serialize frames over a socket or message queue. Each adds at least one full copy of the image plus serialization cost, which often exceeds the compute time for a single Canny pass. A shared-memory view removes that overhead at the language boundary.

**Scope of the claim:** "Zero-copy" here applies to the Python-to-C++ hand-off. Network transfer from S3 and image decode/encode still touch the data, as they must.

### 2. In-Memory Cloud Streaming vs. Ephemeral Disk Bottlenecks

**Decision:** Objects are read from S3 into `io.BytesIO`, decoded in RAM, processed, encoded in RAM and uploaded back. Nothing is written to the container filesystem.

**Why:** Container disks are slow, small and often a shared resource. Disk writes also leave residual data behind, which is undesirable for user media. A RAM-only path keeps latency predictable, lets containers run with a read-only root filesystem, and removes cleanup logic and a class of failure modes (full disks, leftover temp files).

**Trade-off:** Peak memory scales with image size and concurrency, so request size limits and worker counts should be sized accordingly.

### 3. Scoped C++ Linkage vs. Monolithic OpenCV Binaries

**Decision:** `CMakeLists.txt` requests only the modules the engine uses:

```cmake
find_package(OpenCV REQUIRED COMPONENTS core imgproc)
```

so the extension links only `opencv_core` and `opencv_imgproc`.

**Why:** A plain `find_package(OpenCV REQUIRED)` links every module (`stitching`, `dnn`, `video`, and so on). The runtime image must then contain every one of those shared libraries, or the extension fails to import at startup with errors such as `libopencv_stitching.so: cannot open shared object file`. Scoping the link line shrinks the runtime image, reduces the attack surface and makes the dependency set explicit. CI verifies it on every build (see [Verification](#verification--observability)).

### 4. SigV4 Presigned Architecture vs. Public Bucket Exposure

**Decision:** The bucket has **Block All Public Access** enabled. The service authenticates as a dedicated, non-root IAM user with least-privilege permissions, and clients download results through short-lived (60 minute) presigned URLs signed with **AWS Signature Version 4** (`s3v4`, `AWS4-HMAC-SHA256`).

**Why:** Public buckets are a recurring source of data leaks. Presigned URLs give clients time-limited access to exactly one object, with no credentials shared and no bucket policy loosened. SigV4 is mandatory in newer regions such as `eu-north-1`, so the client is explicitly configured for it with the region set.

Example minimal IAM policy for the service user:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/*"
    }
  ]
}
```

### 5. Mitigating Container Startup Race Conditions via Docker Healthchecks

**Decision:** PostgreSQL defines a healthcheck, and the API service depends on it being healthy rather than merely started:

```yaml
db:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U admin -d mediadb"]
    interval: 5s
    timeout: 5s
    retries: 10

api:
  depends_on:
    db:
      condition: service_healthy
```

**Why:** `depends_on` alone only waits for the container to *start*, not for the database to accept connections. The API's `asyncpg` pool is created at startup, so without a healthcheck the first boot can crash with connection errors and enter a restart loop. Gating on `service_healthy` removes that race deterministically.

### 6. Database-Level Percentile Aggregation (`PERCENTILE_CONT`)

**Decision:** Latency percentiles are computed inside PostgreSQL:

```sql
SELECT
  COUNT(*)                                                        AS total_requests,
  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cpp_latency_ms)    AS p50_ms,
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cpp_latency_ms)    AS p95_ms,
  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cpp_latency_ms)    AS p99_ms
FROM process_metrics;
```

**Why:** Averages hide tail latency, and tail latency is what users feel. Computing percentiles in SQL avoids pulling raw rows into the application, keeps the API stateless, and lets the same query be run ad hoc from `psql`. Writes go through an `asyncpg` pool, so telemetry never blocks the event loop.

---

## Complete Tech Stack

| Category | Technology | Version | Role |
|---|---|---|---|
| Native compute | C++ | 17 | Edge detection engine |
| Image processing | OpenCV (`core`, `imgproc`) | 4.x | Canny implementation, `cv::Mat` |
| Language binding | pybind11 | latest | Buffer-protocol bridge to NumPy |
| Build system | CMake | 3.18+ | Builds the `native_engine` extension |
| API runtime | Python | 3.11 | Orchestration and I/O |
| Web framework | FastAPI | latest | Async REST gateway, OpenAPI docs |
| ASGI server | Uvicorn | latest | High-throughput async server |
| Array interchange | NumPy | latest | Zero-copy image buffers |
| Cloud storage | AWS S3 | n/a | Private object storage, SigV4 presigned URLs |
| Cloud SDK | boto3 | latest | S3 access and URL signing |
| Database | PostgreSQL | 15 | Telemetry storage and percentile analytics |
| DB driver | asyncpg | latest | Non-blocking connection pooling |
| Containers | Docker (multi-stage) | n/a | Reproducible build and slim runtime |
| Orchestration | Docker Compose | v2 | API + database with healthcheck gating |
| CI/CD | GitHub Actions, Buildx | n/a | Cached container builds, linkage checks |

---

## Repository Layout

```
.
├── .github/
│   └── workflows/
│       └── ci.yml                # Buildx + type=gha cache, linkage sanity check
├── cpp_engine/
│   ├── include/                  # C++ headers
│   └── src/
│       ├── processor.cpp         # Canny edge detection (OpenCV core + imgproc)
│       └── bindings.cpp          # pybind11 module: native_engine
├── python_service/
│   ├── requirements.txt
│   └── app/
│       └── main.py               # FastAPI app, S3 + asyncpg integration
├── CMakeLists.txt                # Scoped OpenCV linkage, pybind11 module
├── Dockerfile                    # Multi-stage: builder -> slim runtime
├── docker-compose.yml            # api + db (with healthcheck)
├── .env.example                  # Template for required environment variables
└── README.md
```

---

## API Reference

Interactive OpenAPI documentation is served at `http://localhost:8000/docs`.

### `POST /api/v1/process-s3-image`

Fetches an image from S3, runs edge detection in C++, writes the result back to S3, logs telemetry to PostgreSQL and returns a presigned download URL.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `s3_key` | query | string | yes | Key of the source image in the configured bucket |

```bash
curl -X POST "http://localhost:8000/api/v1/process-s3-image?s3_key=uploads/sample.jpg"
```

**Response `200 OK`**

```json
{
  "status": "success",
  "input_s3_key": "uploads/sample.jpg",
  "output_s3_key": "processed/sample_edges.jpg",
  "resolution": "1920x1080",
  "cpp_latency_ms": 12.48,
  "presigned_url": "https://<BUCKET_NAME>.s3.eu-north-1.amazonaws.com/processed/sample_edges.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=3600&...",
  "expires_in_seconds": 3600
}
```

### `POST /api/v1/process-edges`

Accepts a multipart image upload, processes it in C++ and streams back the processed image. The native processing time is returned in a response header.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `file` | multipart form | file | yes | Image to process (JPEG or PNG) |

```bash
curl -X POST "http://localhost:8000/api/v1/process-edges" \
  -F "file=@sample.jpg" \
  -o edges.jpg -D -
```

**Response `200 OK`** returns the processed image as a binary stream, with headers such as:

```
HTTP/1.1 200 OK
content-type: image/jpeg
x-cpp-latency-ms: 11.92
```

### `GET /api/v1/metrics`

Returns real-time latency percentiles computed in PostgreSQL with `PERCENTILE_CONT`.

```bash
curl "http://localhost:8000/api/v1/metrics"
```

**Response `200 OK`**

```json
{
  "total_requests": 1284,
  "cpp_latency_ms": {
    "p50": 11.7,
    "p95": 18.9,
    "p99": 27.4
  }
}
```

### `GET /health`

Basic liveness probe for load balancers and orchestrators.

**Response `200 OK`**

```json
{ "status": "ok" }
```

### Telemetry Schema

```sql
CREATE TABLE IF NOT EXISTS process_metrics (
    id              BIGSERIAL PRIMARY KEY,
    input_s3_key    TEXT        NOT NULL,
    output_s3_key   TEXT        NOT NULL,
    resolution      TEXT        NOT NULL,
    cpp_latency_ms  DOUBLE PRECISION NOT NULL,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## Local Deployment & Quickstart

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2
- An AWS account with a **private** S3 bucket (Block All Public Access enabled)
- An IAM user with `s3:GetObject` and `s3:PutObject` on that bucket (see the policy above)
- Git

### 1. Clone the repository

```bash
git clone https://github.com/<USERNAME>/<REPO>.git
cd <REPO>
```

### 2. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your values. Example:

```env
AWS_ACCESS_KEY_ID=<your-access-key-id>
AWS_SECRET_ACCESS_KEY=<your-secret-access-key>
AWS_REGION=eu-north-1
S3_BUCKET_NAME=<your-bucket-name>

POSTGRES_USER=admin
POSTGRES_PASSWORD=<choose-a-password>
POSTGRES_DB=mediadb
```

> `.env` contains secrets. Never commit it; keep it listed in `.gitignore`.

### 3. Build and start

```bash
docker compose up --build
```

On the first run, the builder stage compiles the C++ extension. PostgreSQL must report healthy before the API starts.

### 4. Verify

Open **http://localhost:8000/docs** for the interactive API documentation, or:

```bash
curl http://localhost:8000/health
```

To stop the stack:

```bash
docker compose down        # keep database volume
docker compose down -v     # also remove database volume
```

---

## Verification & Observability

### Query telemetry directly in PostgreSQL

```bash
# Latest 10 processed images
docker compose exec db psql -U admin -d mediadb -c \
  "SELECT input_s3_key, resolution, cpp_latency_ms, processed_at
   FROM process_metrics ORDER BY processed_at DESC LIMIT 10;"

# Latency percentiles
docker compose exec db psql -U admin -d mediadb -c \
  "SELECT
     PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cpp_latency_ms) AS p50_ms,
     PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cpp_latency_ms) AS p95_ms,
     PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cpp_latency_ms) AS p99_ms
   FROM process_metrics;"
```

### Verify native runtime linkage inside the container

Confirms that the compiled extension imports cleanly and that no shared library is missing:

```bash
# The extension loads without ImportError
docker compose run --rm --entrypoint python api -c "import native_engine; print('native_engine OK')"

# No unresolved shared-library dependencies
docker compose run --rm --entrypoint sh api -c \
  "ldd /app/native_engine*.so | grep 'not found' && exit 1 || echo 'all shared libraries resolved'"

# Inspect exactly which OpenCV modules the extension links against
docker compose run --rm --entrypoint sh api -c "ldd /app/native_engine*.so | grep opencv"
```

The last command should list only `opencv_core` and `opencv_imgproc`.

### Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request. It:

1. Builds the multi-stage image with Docker Buildx, using the GitHub Actions cache (`type=gha`) for fast incremental builds.
2. Runs the dynamic-linkage sanity check above inside the built container, failing the pipeline if `native_engine` cannot load or any `.so` dependency is missing.

---

