# 🎙️ Real-Time Voice AI Agent (RAG + STT + TTS)

A low-latency, production-ready voice AI agent that supports real-time conversations using speech-to-text, retrieval-augmented generation, and text-to-speech.

Designed for practical deployment in environments like hotels, customer support, and smart assistants.

---

## 🚀 Features

* ⚡ **Low Latency Pipeline**
  Optimized for near real-time interaction (~sub-second response in local testing)

* 🧠 **Retrieval-Augmented Generation (RAG)**
  Uses domain-specific knowledge base for accurate and contextual responses

* 🎤 **Speech-to-Text (STT)**
  Converts live audio input into text

* 🔊 **Text-to-Speech (TTS)**
  Streams AI-generated responses as audio output

* 🔄 **Streaming WebSocket Architecture**
  Enables real-time bidirectional communication

* 🧩 **Modular Graph-Based Execution**
  Built using a node-based pipeline for flexibility and scalability

---

## 🏗️ Architecture

Client (Mic Input)
→ WebSocket Server
→ STT
→ RAG + LLM (Graph Pipeline)
→ TTS
→ Audio Stream Back to Client

---

## 🗂️ Project Structure

```
src/
├── graph/              # Core agent logic (LangGraph pipeline)
├── voice/              # STT & TTS modules
├── rag_creation/       # Knowledge base + embedding pipeline
├── databases/          # SQLite storage
├── index/              # FAISS vector store
├── assets/             # Static assets (audio/UI)
├── prompts/            # Prompt engineering
├── ws_routes.py        # WebSocket endpoints
└── main.py             # Entry point
```

---

## ⚙️ Tech Stack

* Python (FastAPI / Uvicorn / Gunicorn)
* WebSockets for real-time communication
* FAISS for vector search
* LangGraph / LangChain
* OpenAI / LLM APIs
* Deepgram / Whisper (STT)
* ElevenLabs / TTS APIs

---

## 🧪 Setup

### 1. Clone the repo

```
git clone <your-repo-url>
cd <project>
```

### 2. Create virtual environment

```
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 3. Install dependencies

```
pip install -r requirements.txt
```

### 4. Setup environment variables

Create `.env` file using `.env.example`:

```
OPENAI_API_KEY=your_key
DEEPGRAM_API_KEY=your_key
ELEVENLABS_API_KEY=your_key
```

---

## ▶️ Running the Server

### Development

```
uvicorn src.main:app --reload
```

### Production

```
gunicorn -k uvicorn.workers.UvicornWorker src.main:app
```

---

## 📡 Usage

* Connect via WebSocket
* Stream audio input
* Receive real-time audio responses

---

## 💡 Use Cases

* 🏨 Hotel voice assistant (room service, FAQs)
* 📞 Customer support automation
* 🧠 Personal AI assistants
* 🎓 Interactive learning systems

---

## 📈 Future Improvements

* Multi-language support
* Emotion-aware voice synthesis
* Edge deployment optimization
* Improved latency benchmarking

---

## 🤝 Contribution

Open to improvements, optimizations, and feature additions.

---

## 📬 Contact

Built by Basil
AI & Data Science Engineer
