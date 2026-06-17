"""Layer 2: MiniLM ONNX binary classifier for prompt injection.

Loads a fine-tuned MiniLM-L6-v2 model (~22MB) compiled to ONNX. Runs
fully embedded — no external service call. ~8ms per inference.

Graceful degradation: if ``onnxruntime`` or ``transformers`` is not
installed, or the model file is missing, the layer is unavailable and
``score()`` returns a zero-confidence result.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ClassifierResult:
    """Result of the ONNX classifier layer."""

    score: float = 0.0  # injection probability 0.0–1.0
    available: bool = False


class OnnxClassifierLayer:
    """ONNX-backed MiniLM binary classifier.

    The model file is expected at ``model_path`` (baked into the Docker
    image at build time). If the file or dependencies are missing, the
    layer silently degrades — callers should check ``is_available()``.
    """

    def __init__(self, model_path: str, max_length: int = 512) -> None:
        self._model_path = model_path
        self._max_length = max_length
        self._session = None
        self._tokenizer = None
        self._available = False

        if not os.path.isfile(model_path):
            logger.debug("ONNX model not found at %s — classifier disabled", model_path)
            return

        try:
            import numpy as np
            import onnxruntime as rt
            from transformers import AutoTokenizer

            self._rt = rt
            self._np = np
            self._session = rt.InferenceSession(model_path)
            tokenizer_path = os.path.dirname(model_path)
            self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
            self._available = True
            logger.info("ONNX injection classifier loaded from %s", model_path)
        except ImportError:
            logger.debug(
                "onnxruntime/transformers not installed — classifier disabled. "
                "Install with: pip install 'webgateway[injection]'"
            )
        except Exception as exc:
            logger.warning("Failed to load ONNX classifier: %s", exc)

    def is_available(self) -> bool:
        return self._available

    def score(self, content: str) -> ClassifierResult:
        """Score *content* for injection probability.

        Returns ``ClassifierResult(score=0.0, available=False)`` if the
        layer is not loaded.
        """
        if not self._available or not content:
            return ClassifierResult(available=self._available)

        try:
            inputs = self._tokenizer(
                content[: self._max_length],
                return_tensors="np",
                truncation=True,
                padding=True,
            )
            expected = {i.name for i in self._session.get_inputs()}
            filtered = {k: v for k, v in dict(inputs).items() if k in expected}
            logits = self._session.run(None, filtered)[0][0]
            shifted = logits - self._np.max(logits)
            exp_vals = self._np.exp(shifted)
            probabilities = exp_vals / self._np.sum(exp_vals)
            # Index 1 = injection class (binary classifier)
            if len(probabilities) > 1:
                injection_prob = float(probabilities[1])
            else:
                injection_prob = float(probabilities[0])
            return ClassifierResult(score=injection_prob, available=True)
        except Exception as exc:
            logger.warning("ONNX classifier inference failed: %s", exc)
            return ClassifierResult(available=self._available)
