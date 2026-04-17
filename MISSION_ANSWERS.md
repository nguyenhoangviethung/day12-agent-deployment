# Day 12 Lab - Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found
1. Hardcoded API key in code (`OPENAI_API_KEY`).
2. Hardcoded database URL with credentials.
3. Debug/reload enabled for a production-like run.
4. Logs secrets via `print` (leaks API key).
5. No health check endpoint.
6. Hardcoded host/port (`localhost:8000`) instead of env vars.
7. No graceful shutdown handling.

### Exercise 1.2: Basic version observation
Chạy được local, nhưng chưa production-ready vì còn hardcoded secrets/port, thiếu health checks và graceful shutdown.

### Exercise 1.3: Comparison table
| Feature | Develop | Production | Why Important? |
|---------|---------|------------|----------------|
| Config | Hardcoded constants | Env vars via `config.py` | Portable config across environments |
| Secrets | Inline strings | Read from env/.env | Avoid leaking secrets |
| Logging | `print()` | Structured JSON logging | Easier monitoring and search |
| Health check | None | `/health` endpoint | Enables liveness/readiness checks |
| Shutdown | None | Graceful shutdown | Avoid dropped requests |
| Host/Port | `localhost:8000` | `0.0.0.0` + `PORT` | Works in containers/cloud |
| CORS | Not set | Configurable allowed origins | Safer cross-origin access |
| Lifespan | None | Startup/shutdown hooks | Safe init/cleanup |

## Part 2: Docker

### Exercise 2.1: Dockerfile questions
1. Base image: `python:3.11` (develop Dockerfile).
2. Working directory: `/app`.
3. Copy `requirements.txt` first to leverage layer cache so deps only reinstall when requirements change.
4. CMD vs ENTRYPOINT: `CMD` provides default args and is easier to override; `ENTRYPOINT` defines the main command and is harder to override.

### Exercise 2.2: Image size (develop)
- Image size (develop): 1.66GB (`my-agent:develop`).

### Exercise 2.3: Image size comparison
- Develop: 1.66GB (image: `my-agent:develop` / `my-agent:latest`)
- Production: 236MB (image: `my-agent:advanced` / `production-agent:latest`)
- Difference: ~85.8% smaller (about 1.424GB less)

### Exercise 2.4: Docker Compose stack
- Services: `agent`, `redis`, `qdrant`, `nginx`.
- Communication: Nginx reverse-proxy routes external traffic to `agent`; `agent` connects to `redis` (cache/session) and `qdrant` (vector DB) over the internal network.
- Ports: public traffic enters via Nginx (80/443); agent itself is not exposed directly.

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment
- URL: https://day12-00164-production.up.railway.app
- Screenshot: `static/03/part3-railway-dashboard.png`
- Test (curl): `/health` returns `{"status":"ok"}` and `/ask` returns 200 with mock response.

#### Screenshot evidence
- `static/03/part3-railway-dashboard.png`: ![Part 3 Railway dashboard](static/03/part3-railway-dashboard.png)
- `static/03/part3-health-200.png`: ![Part 3 Health 200](static/03/part3-health-200.png)

### Exercise 3.2: Render vs Railway config
- Render (`render.yaml`): blueprint defines a web service + Redis add-on, includes `buildCommand`, `startCommand`, `healthCheckPath`, region/plan, autoDeploy, and can auto-generate secrets (e.g., `AGENT_API_KEY`).
- Railway (`railway.toml`): uses Nixpacks builder, `startCommand`, healthcheck path/timeout, and restart policy; env vars are set via Railway dashboard/CLI (no IaC Redis service in this file).

### Exercise 3.3: Cloud Run (optional)
- `cloudbuild.yaml`: CI/CD pipeline runs tests, builds and pushes Docker image, then deploys to Cloud Run with env/secrets.
- `service.yaml`: defines Cloud Run service, autoscaling limits, container resources, env vars, and liveness/startup probes.

## Part 4: API Security

### Exercise 4.1-4.3: Answers
- API key check: `verify_api_key` reads `X-API-Key` header, compares with `AGENT_API_KEY`. Missing key → 401, invalid key → 403. Rotate by changing the env var and restarting the app.
- JWT flow: `/auth/token` verifies user credentials, creates HS256 JWT with `sub`, `role`, `iat`, `exp`; client sends `Authorization: Bearer <token>`; invalid/expired tokens return 401/403.
- Rate limiting: Sliding Window Counter; user limit = 10 req/min, admin limit = 100 req/min. Admin bypass uses `rate_limiter_admin` based on role.

### Exercise 4.1-4.3: Test results
Local tests (04-api-gateway/production running at http://localhost:8040):

1) Wrong method (GET /ask) → 405
```
{"detail":"Method Not Allowed"}
```

2) API key (POST /ask?question=hello) → 200
```
{"question":"hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

3) Token issuance (student/demo123) → 200
```
{"access_token":"<redacted>","token_type":"bearer","expires_in_minutes":60,
 "hint":"Include in header: Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."}
```

4) Invalid token → 401
```
{"detail":"Invalid token."}
```

5) Ask with valid JWT → 200
```
{"question":"Cloud Deployment là gì?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.",
 "usage":{"requests_remaining":9,"budget_remaining_usd":2e-05}}
```

6) Rate limit (10 req/min) — 20 rapid calls:
```
1-10: 200
11-20: 429
```
Sample 429 body:
```
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

7) Usage endpoint (GET /me/usage) → 200
```
{"user_id":"student","date":"2026-04-17","requests":10,"input_tokens":100,"output_tokens":308,
 "cost_usd":0.0002,"budget_usd":1.0,"budget_remaining_usd":0.9998,"budget_used_pct":0.0}
```

### Exercise 4.4: Cost guard implementation
In 04-api-gateway/production/cost_guard.py:
- Per-user daily budget: $1/day; global daily budget: $10/day.
- `check_budget()` blocks when user budget exceeded (HTTP 402) or global budget exceeded (HTTP 503).
- Warns when reaching 80% of budget.
- `record_usage()` increments token counts and cost, tracks per-user usage and global cost.

#### Screenshot evidence (đặt cuối Part 4)
- `static/04/part4-token-issue-200.png`: ![Part 4 Token Issue 200](static/04/part4-token-issue-200.png)
- `screenshots/part4-auth-required-401.png`: Gọi API không có token/API key và nhận 401.
- `screenshots/part4-rate-limit-429.png`: Kết quả khi vượt giới hạn request.
- `screenshots/part4-usage-endpoint-200.png`: Kết quả endpoint usage trả 200.

## Part 5: Scaling & Reliability

### Exercise 5.1-5.5: Test results (port 8050)
Stack: `docker compose up --scale agent=3` (Nginx LB → http://localhost:8050)

1) Health check → 200
```
{
	"status": "ok",
	"instance_id": "instance-ac48c2",
	"uptime_seconds": 151.7,
	"storage": "redis",
	"redis_connected": true,
	"shutting_down": false,
	"in_flight_requests": 1
}
```

2) Readiness → 200
```
{
	"ready": true,
	"instance": "instance-96547b",
	"in_flight_requests": 1
}
```

3) Load balancing (5 requests to `/chat`)
```
1: served_by=instance-9d4016
2: served_by=instance-ac48c2
3: served_by=instance-96547b
4: served_by=instance-9d4016
5: served_by=instance-ac48c2
instances: ['instance-96547b', 'instance-9d4016', 'instance-ac48c2']
```

4) Stateless test (`python test_stateless.py`)
```
Instances used: {'instance-96547b', 'instance-9d4016', 'instance-ac48c2'}
Total messages: 10
✅ Session history preserved across all instances via Redis!
```


## Part 6: Final Project

### Project root and delivered files
- Root cho Part 6: `06-lab-complete/`.
- Đã có đầy đủ các file yêu cầu trong code lab:
	- `app/main.py`, `app/config.py`, `app/auth.py`, `app/rate_limiter.py`, `app/cost_guard.py`
	- `utils/mock_llm.py`
	- `Dockerfile`, `docker-compose.yml`, `requirements.txt`
	- `.env.example`, `.dockerignore`, `railway.toml` (kèm `render.yaml`)

### Requirement coverage (Part 6)
Functional:
- [x] REST API trả lời câu hỏi qua `POST /ask`
- [x] Conversation history (Redis-backed, fallback memory)
- [ ] Streaming responses (optional, chưa bật)

Non-functional:
- [x] Dockerized với multi-stage Dockerfile
- [x] Config từ environment variables
- [x] API key authentication (`X-API-Key`)
- [x] Rate limiting (sliding window)
- [x] Cost guard theo budget ngày
- [x] Health check (`GET /health`)
- [x] Readiness check (`GET /ready`)
- [x] Graceful shutdown (`SIGTERM`, `SIGINT`)
- [x] Stateless design (state trong Redis, có fallback memory)
- [x] Structured JSON logging
- [x] Deploy thành công lên Railway

### Deployment result (Railway)
- Public URL: https://2a202600164-nguyenhoangviethung-production.up.railway.app
- `railway up` đã thành công (exit code 0).
- Smoke test:
	- `GET /health` trả 200 và `status: "ok"`
	- `POST /ask` không có API key trả 401
	- `POST /ask` với API key hợp lệ trả 200 và response JSON đúng format

#### Screenshot evidence (đặt cuối Part 6)
- `static/06/part6-railway-up-log.png`: ![Part 6 Railway Deploy Success](static/06/part6-railway-up-log.png)
- `static/06/part6-docs-ui.png`: ![Part 6 Swagger Docs](static/06/part6-docs-ui.png)
- `static/06/part6-health-200.png`: ![Part 6 Health 200](static/06/part6-health-200.png)
- `static/06/part6-ask-200.png`: ![Part 6 Ask 200](static/06/part6-ask-200.png)
- `static/06/part6-ready-200.png`: ![Part 6 Ready 200](static/06/part6-ready-200.png)
- `static/06/part6-root-endpoint-200.png`: ![Part 6 Root Endpoint 200](static/06/part6-root-endpoint-200.png)

### Notes
- `OPENAI_API_KEY` chưa set thì app chạy mock LLM (không block deploy).
- API key đã từng xuất hiện trong terminal/chat nên cần rotate trước khi nộp final.
