from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

@app.get("/health", response_class=JSONResponse)
def health():
    return {
        "status": "ok"
    }

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Otro tipo de source cache a ver si ahora si d</title>
    </head>
    <body>
        <h1>Hola test hola hola 2</h1>
        <p>FastAPI funcionando correctamente</p>
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
