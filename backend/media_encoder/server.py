"""Local multilingual E5 retrieval. No text or queries are logged or sent outside."""

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI
from huggingface_hub import hf_hub_download
from onnxruntime.quantization import QuantType, quantize_dynamic
from pydantic import BaseModel, Field
from tokenizers import Tokenizer

MODEL = "intfloat/multilingual-e5-small"
VERSION = MODEL + ":int8:v1"
lock = threading.Lock()
session = None
tokenizer = None


def load():
    global session, tokenizer
    directory = Path(os.getenv("MODEL_CACHE", "/models"))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "multilingual-e5-small-int8.onnx"
    if not target.exists():
        original = hf_hub_download(MODEL, "onnx/model.onnx")
        temporary = directory / "multilingual-e5-small-int8.tmp.onnx"
        quantize_dynamic(
            original, str(temporary), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"]
        )
        temporary.replace(target)
    token_file = hf_hub_download(MODEL, "tokenizer.json")
    tokenizer = Tokenizer.from_file(token_file)
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding(pad_id=1, pad_token="<pad>")  # noqa: S106 - tokenizer vocabulary
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.getenv("ENCODER_THREADS", "4"))
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(target), sess_options=options, providers=["CPUExecutionProvider"]
    )
    encode(["Готов к поиску"], True)


def encode(texts, query):
    with lock:
        tokens = tokenizer.encode_batch([("query: " if query else "passage: ") + t for t in texts])
        ids = np.asarray([t.ids for t in tokens], dtype=np.int64)
        mask = np.asarray([t.attention_mask for t in tokens], dtype=np.int64)
        inputs = {"input_ids": ids, "attention_mask": mask}
        names = {i.name for i in session.get_inputs()}
        if "token_type_ids" in names:
            inputs["token_type_ids"] = np.zeros_like(ids)
        hidden = session.run(None, {k: v for k, v in inputs.items() if k in names})[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1)
        pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        return pooled.astype(np.float32).tolist()


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(load)
    yield


app = FastAPI(lifespan=lifespan)


class Request(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)
    query: bool = False


@app.get("/health")
def health():
    return {"ready": session is not None, "model": VERSION, "dimensions": 384}


@app.post("/encode")
def embeddings(body: Request):
    from fastapi import HTTPException

    if any(not t.strip() or len(t) > 12000 for t in body.texts):
        raise HTTPException(422, "Expected nonempty texts up to 12000 characters")
    return {"model": VERSION, "vectors": encode(body.texts, body.query)}
