"""Resume initial indexing over a private SSH pipe, using the identical ONNX model.

Requires numpy, onnxruntime and tokenizers. No media text is saved locally.
The --model-dir contains the server's model.onnx and tokenizer.json copies.
"""

import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--ssh", required=True, help="SSH destination, user@host")
    parser.add_argument("--threads", type=int, default=6)
    args = parser.parse_args()
    token = Tokenizer.from_file(str(args.model_dir / "tokenizer.json"))
    token.enable_truncation(max_length=512)
    token.enable_padding(pad_id=1, pad_token="<pad>")  # noqa: S106 - tokenizer vocabulary
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = args.threads, 1
    session = ort.InferenceSession(
        str(args.model_dir / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
    )
    names = {entry.name for entry in session.get_inputs()}

    def encode(texts):
        vectors = []
        for start in range(0, len(texts), 16):
            tokens = token.encode_batch(["passage: " + t for t in texts[start : start + 16]])
            ids = np.asarray([t.ids for t in tokens], dtype=np.int64)
            mask = np.asarray([t.attention_mask for t in tokens], dtype=np.int64)
            inputs = {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
            }
            hidden = session.run(None, {k: v for k, v in inputs.items() if k in names})[0]
            weights = mask[..., None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            vectors.extend(pooled.astype(np.float32).tolist())
        return vectors

    # Exact server-generated model data and identical pooling/prefixes are used.
    ssh = shutil.which("ssh")
    if not ssh or not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+", args.ssh):
        raise ValueError("Expected an installed SSH client and user@host")
    command = [
        ssh,
        "-T",
        args.ssh,
        "docker",
        "exec",
        "-i",
        "pikirler-prod-api-1",
        "python",
        "-u",
        "/srv/app/tools/media_embedding_bridge.py",
    ]
    process = subprocess.Popen(  # noqa: S603 - validated SSH destination, no shell
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        bufsize=1,
    )
    total, started = 0, time.monotonic()
    try:
        for line in process.stdout:
            request = json.loads(line)
            if request.get("done"):
                print(
                    f"Finished local assistance: {total} passages "
                    f"in {time.monotonic() - started:.1f}s",
                    flush=True,
                )
                break
            assert request["model"] == "intfloat/multilingual-e5-small:int8:v1"
            vectors = encode(request["texts"])
            process.stdin.write(json.dumps({"model": request["model"], "vectors": vectors}) + "\n")
            process.stdin.flush()
            total += len(vectors)
            print(
                f"Encoded {total} passages; {total / max(time.monotonic() - started, 0.01):.1f}/s",
                flush=True,
            )
    finally:
        process.stdin.close()
        code = process.wait(timeout=30)
        if code:
            raise SystemExit(code)


if __name__ == "__main__":
    main()
