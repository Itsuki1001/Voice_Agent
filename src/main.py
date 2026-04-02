from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ws_routes import router

app = FastAPI(title="Voice Agent")

app.include_router(router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

  # ← registers the WebSocket routes onto app