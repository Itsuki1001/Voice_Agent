from fastapi import APIRouter, Request, HTTPException, Header
# from .handler import process_chat_message
import asyncio




router = APIRouter()


@router.post("/chat")
async def chat(request: Request):


    data = await request.json()
    user_message = data.get("message")
    if not user_message:
        raise HTTPException(status_code=400, detail="Missing 'message' in request body")

    user_id = request.query_params.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing or empty 'user_id' query parameter")



    reply = user_id
    
    if reply is None:
        # This means either a human reply was handled or an error occurred
        return {"reply": ""}

    return {"reply": reply}