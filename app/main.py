from fastapi import FastAPI

app = FastAPI(title="Simlexx Wealth API")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
