#!/usr/bin/env python3
"""Download pre-exported ONNX model + tokenizer from HuggingFace Hub.

The model protectai/deberta-v3-base-prompt-injection-v2 ships with
an ``onnx/`` subdirectory containing model.onnx + tokenizer files.
We download just those — no torch or optimum required.
"""

import os
import sys

MODEL_ID = "protectai/deberta-v3-base-prompt-injection-v2"
DEST_DIR = "/app/models"


def main() -> None:
    os.makedirs(DEST_DIR, exist_ok=True)

    model_file = os.path.join(DEST_DIR, "model.onnx")
    if os.path.isfile(model_file):
        size_mb = os.path.getsize(model_file) / 1024 / 1024
        print(f"Model already exists at {model_file} ({size_mb:.1f} MB), skipping")
        return

    print(f"Downloading ONNX model from HuggingFace: {MODEL_ID}/onnx/")

    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=MODEL_ID,
            allow_patterns=["onnx/*"],
            local_dir=DEST_DIR + "/_dl",
        )

        import shutil

        src = os.path.join(DEST_DIR, "_dl", "onnx")
        for name in os.listdir(src):
            shutil.move(os.path.join(src, name), os.path.join(DEST_DIR, name))
        shutil.rmtree(DEST_DIR + "/_dl")

        size_mb = os.path.getsize(model_file) / 1024 / 1024
        print(f"Model saved to {model_file} ({size_mb:.1f} MB)")
        print(f"Tokenizer files saved to {DEST_DIR}")

    except Exception as exc:
        print(f"WARNING: Failed to download model: {exc}", file=sys.stderr)
        print("Injection classifier will be disabled (graceful degradation)", file=sys.stderr)


if __name__ == "__main__":
    main()
