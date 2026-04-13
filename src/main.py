from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from ws_routes import router as ws_router
# from whatsapp.webhook import router as whatsapp_router
# from whatsapp.webhook import start_workers
#from chat.chat_webhook import router as chat_router


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     start_workers()
#     yield


app = FastAPI(
    title="Voice Agent"
    #lifespan=lifespan  # 👈 THIS IS THE MISSING PIECE
)

app.include_router(ws_router)
# app.include_router(whatsapp_router)
#app.include_router(chat_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)