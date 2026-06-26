import json
import os
import socket
import threading
import time
from multiprocessing import Process, cpu_count
from urllib.request import urlopen

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

# En Fargate (y ECS en general) este endpoint expone metadata y stats del task.
ECS_METADATA_URI = os.getenv("ECS_CONTAINER_METADATA_URI_V4")


def _get_json(url, timeout=2):
    with urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get_text(url, timeout=2):
    with urlopen(url, timeout=timeout) as resp:
        return resp.read().decode().strip()


def _private_ip():
    """IP privada con la que el contenedor sale a la red (la del ENI en Fargate)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _public_ip():
    """IP pública de salida (egress). En Fargate suele ser la del NAT o la IP pública del task."""
    for url in ("https://checkip.amazonaws.com", "https://api.ipify.org"):
        try:
            return _get_text(url)
        except Exception:
            continue
    return None


def _cpu_percent(stats):
    """Calcula el % de CPU al estilo Docker a partir de los stats del task."""
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        if sys_delta > 0 and cpu_delta >= 0:
            return round((cpu_delta / sys_delta) * online * 100.0, 2)
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return None


# ----------------------------- API JSON -----------------------------


@app.get("/api/ip", response_class=JSONResponse)
def api_ip():
    info = {"hostname": socket.gethostname()}

    try:
        info["private_ip"] = _private_ip()
    except Exception as e:
        info["private_ip"] = f"error: {e}"

    info["public_ip"] = _public_ip()

    if ECS_METADATA_URI:
        try:
            task = _get_json(ECS_METADATA_URI + "/task")
            info["ecs"] = {
                "cluster": task.get("Cluster"),
                "task_arn": task.get("TaskARN"),
                "availability_zone": task.get("AvailabilityZone"),
                "launch_type": task.get("LaunchType"),
            }
        except Exception as e:
            info["ecs"] = f"error: {e}"
    else:
        info["ecs"] = None  # corriendo fuera de Fargate (ej. local)

    return info


@app.get("/api/stats", response_class=JSONResponse)
def api_stats():
    if not ECS_METADATA_URI:
        return JSONResponse(
            {"error": "Sin metadata de ECS (¿corriendo en local, no en Fargate?)"},
            status_code=404,
        )
    try:
        stats = _get_json(ECS_METADATA_URI + "/stats")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    mem = stats.get("memory_stats", {})
    mem_usage = mem.get("usage")
    mem_limit = mem.get("limit")
    return {
        "cpu_percent": _cpu_percent(stats),
        "memory_usage_mb": round(mem_usage / 1024 / 1024, 1) if mem_usage else None,
        "memory_limit_mb": round(mem_limit / 1024 / 1024, 1) if mem_limit else None,
        "memory_percent": round(mem_usage / mem_limit * 100, 1)
        if mem_usage and mem_limit
        else None,
    }


def _busy_until(deadline):
    while time.time() < deadline:
        pass


def _run_burn(seconds, workers):
    deadline = time.time() + seconds
    procs = [Process(target=_busy_until, args=(deadline,)) for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:  # join para no dejar zombies
        p.join()


@app.get("/api/burn", response_class=JSONResponse)
def api_burn(seconds: int = 30, workers: int = 0):
    workers = workers or cpu_count() or 1
    threading.Thread(target=_run_burn, args=(seconds, workers), daemon=True).start()
    return {"burning": True, "seconds": seconds, "workers": workers}


# ----------------------------- UI -----------------------------


@app.get("/health", response_class=JSONResponse)
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def root():
    return PAGE


PAGE = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fargate test</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; padding: 2rem;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: #0f172a; color: #e2e8f0;
      display: flex; justify-content: center;
    }
    .wrap { width: 100%; max-width: 760px; }
    h1 { font-size: 1.4rem; margin: 0 0 1.5rem; }
    .card {
      background: #1e293b; border: 1px solid #334155; border-radius: 12px;
      padding: 1.25rem 1.5rem; margin-bottom: 1.25rem;
    }
    .card h2 { font-size: .85rem; text-transform: uppercase; letter-spacing: .05em;
      color: #94a3b8; margin: 0 0 1rem; }
    .row { display: flex; justify-content: space-between; gap: 1rem;
      padding: .4rem 0; border-bottom: 1px solid #293548; font-size: .95rem; }
    .row:last-child { border-bottom: 0; }
    .row .k { color: #94a3b8; }
    .row .v { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      text-align: right; word-break: break-all; }
    .bar { height: 10px; background: #334155; border-radius: 6px; overflow: hidden; margin-top: .3rem; }
    .bar > i { display: block; height: 100%; width: 0;
      background: linear-gradient(90deg,#22c55e,#eab308,#ef4444); transition: width .4s; }
    .big { font-size: 2rem; font-family: ui-monospace, monospace; }
    button {
      background: #ef4444; color: #fff; border: 0; border-radius: 8px;
      padding: .7rem 1.2rem; font-size: .95rem; cursor: pointer; font-weight: 600;
    }
    button:hover { background: #dc2626; }
    button:disabled { background: #475569; cursor: not-allowed; }
    .controls { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; }
    .controls label { font-size: .85rem; color: #94a3b8; }
    input { width: 70px; background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
      border-radius: 6px; padding: .45rem; font-size: .9rem; }
    .muted { color: #64748b; font-size: .8rem; margin-top: .6rem; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>🚀 Fargate test dashboard</h1>

    <div class="card">
      <h2>Dirección IP</h2>
      <div class="row"><span class="k">IP pública (egress)</span><span class="v" id="public_ip">…</span></div>
      <div class="row"><span class="k">IP privada (ENI)</span><span class="v" id="private_ip">…</span></div>
      <div class="row"><span class="k">Hostname</span><span class="v" id="hostname">…</span></div>
      <div class="row"><span class="k">Availability zone</span><span class="v" id="az">…</span></div>
      <div class="row"><span class="k">Task ARN</span><span class="v" id="task_arn">…</span></div>
    </div>

    <div class="card">
      <h2>Stats del contenedor <span style="text-transform:none;color:#64748b">(actualiza cada 1s)</span></h2>
      <div class="row"><span class="k">CPU</span><span class="v big" id="cpu">…</span></div>
      <div class="bar"><i id="cpu_bar"></i></div>
      <div class="row" style="margin-top:1rem"><span class="k">Memoria</span><span class="v" id="mem">…</span></div>
      <div class="bar"><i id="mem_bar"></i></div>
    </div>

    <div class="card">
      <h2>Subir la CPU</h2>
      <div class="controls">
        <label>Segundos <input type="number" id="seconds" value="30" min="1"></label>
        <label>Workers <input type="number" id="workers" value="0" min="0" placeholder="auto"></label>
        <button id="burn">🔥 Quemar CPU</button>
      </div>
      <div class="muted" id="burn_status">Workers = 0 usa todos los vCPU disponibles.</div>
    </div>
  </div>

  <script>
    function set(id, v) { document.getElementById(id).textContent = (v ?? "—"); }

    async function loadIp() {
      try {
        const d = await (await fetch("/api/ip")).json();
        set("public_ip", d.public_ip);
        set("private_ip", d.private_ip);
        set("hostname", d.hostname);
        set("az", d.ecs && d.ecs.availability_zone);
        set("task_arn", d.ecs && d.ecs.task_arn);
      } catch (e) {}
    }

    async function loadStats() {
      try {
        const d = await (await fetch("/api/stats")).json();
        if (d.error) { set("cpu", d.error); return; }
        const cpu = d.cpu_percent;
        set("cpu", cpu != null ? cpu.toFixed(1) + " %" : "—");
        document.getElementById("cpu_bar").style.width = Math.min(cpu || 0, 100) + "%";
        const mp = d.memory_percent;
        set("mem", (d.memory_usage_mb ?? "—") + " / " + (d.memory_limit_mb ?? "—") + " MB"
                   + (mp != null ? "  (" + mp + "%)" : ""));
        document.getElementById("mem_bar").style.width = Math.min(mp || 0, 100) + "%";
      } catch (e) {}
    }

    document.getElementById("burn").addEventListener("click", async () => {
      const s = document.getElementById("seconds").value || 30;
      const w = document.getElementById("workers").value || 0;
      const btn = document.getElementById("burn");
      btn.disabled = true;
      try {
        const d = await (await fetch(`/api/burn?seconds=${s}&workers=${w}`)).json();
        document.getElementById("burn_status").textContent =
          `🔥 Quemando con ${d.workers} workers durante ${d.seconds}s…`;
        setTimeout(() => {
          btn.disabled = false;
          document.getElementById("burn_status").textContent = "Listo. Puedes volver a quemar.";
        }, d.seconds * 1000);
      } catch (e) { btn.disabled = false; }
    });

    loadIp();
    loadStats();
    setInterval(loadStats, 1000);
  </script>
</body>
</html>
"""


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def catch_all(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = await request.body()

    print(body)
    return {"ok": True}
