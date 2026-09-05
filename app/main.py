from fastapi import FastAPI

app = FastAPI(
    title="Simlexx Wealth Creation",
    version="0.0.1"
)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
