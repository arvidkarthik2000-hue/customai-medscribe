"""Ad zone sub-application (stub; filled in by the ad-zone build)."""
from fastapi import FastAPI

app = FastAPI(title="MedScribe ad zone", docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"ok": True, "zone": "ads"}
