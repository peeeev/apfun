"""FastAPI app entrypoint. See CLAUDE.md → Networking: bind to 0.0.0.0:4000."""

from fastapi import FastAPI

from apfun.config import settings

app = FastAPI(title="apfun")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run("apfun.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
