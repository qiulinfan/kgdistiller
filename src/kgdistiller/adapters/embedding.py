"""The embedding model, for the optional ``retrieval`` extra.

An embedding model is a Hugging Face model id of an XLM-RoBERTa sentence
encoder that ships its sentencepiece tokenizer, such as ``BAAI/bge-m3``. It is
loaded with int8 weights (one scale per row, the vocabulary table included)
and tokenized by sentencepiece, so a loaded model holds about a quarter of its
float32 size: measured on 2026-10-15, ``BAAI/bge-m3`` takes about 1 GB on
Apple silicon instead of 3.2 GB. The vectors therefore belong to the model and
this precision together: ``identity`` is what ``meta.embedding`` stores.

The vocabulary table stays in CPU memory and the rest runs on CUDA or Apple's
MPS when present, in float16, else on the CPU in float32. Texts are encoded one
at a time: batching on MPS doubled the process (2 GB) for 2.6 times the speed,
and one at a time needs no padding, so a text's vector never depends on its
neighbours. Pooling comes from the model's ``1_Pooling/config.json`` and the
prompts from its ``config_sentence_transformers.json``. Weights are read
tensor by tensor from a memory map and quantized in row chunks, so loading
never holds the float32 model. Offline use is Hugging Face's own
``HF_HUB_OFFLINE``. Vectors leave this module as little-endian float32 bytes,
L2-normalized. NumPy, PyTorch, transformers and sentencepiece are imported
lazily, so importing this module is cheap.

``encoder(model)`` does not load anything in the calling process: it returns
a client of the one embedding process per user (``embed_service``).
"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from ..home import KnowledgeError

PRECISION = "int8"
DOCUMENT_PROMPTS = ("document", "passage", "corpus")
QUERY_PROMPT = "query"
CHUNK_ROWS = 2048
# XLM-RoBERTa's fairseq vocabulary: sentencepiece ids shift by one past <s>, <pad>, </s>, <unk>.
BOS, EOS, UNK, OFFSET = 0, 2, 3, 1
CONFIG_FILES = ("config.json", "sentencepiece.bpe.model", "1_Pooling/config.json")
OPTIONAL_FILES = ("config_sentence_transformers.json", "sentence_bert_config.json")
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


class EmbeddingError(KnowledgeError):
    """The embedding model could not be loaded or run."""


class RetrievalExtraMissing(EmbeddingError):
    """NumPy, PyTorch, transformers or sentencepiece is not installed."""


def identity(model: str) -> str:
    """What ``meta.embedding`` records for vectors of ``model``: the model id and the precision."""
    return f"{model} {PRECISION}"


def model_of(recorded: str) -> str:
    """The model id of a ``meta.embedding`` value."""
    return recorded.removesuffix(f" {PRECISION}")


def _download(model: str, name: str, required: bool = True) -> Path | None:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, LocalEntryNotFoundError

    try:
        return Path(hf_hub_download(model, name, token=False))
    except (EntryNotFoundError, LocalEntryNotFoundError):
        if required:
            raise
        return None


class _Weights:
    """The checkpoint's tensors, read one at a time from a memory map."""

    def __init__(self, path: Path) -> None:
        import torch

        if path.suffix == ".safetensors":
            from safetensors import safe_open

            handle = safe_open(str(path), framework="pt")
            self._get = handle.get_tensor
            self.keys = set(handle.keys())
        else:
            tensors = torch.load(path, mmap=True, weights_only=True, map_location="cpu")
            self._get = tensors.__getitem__
            self.keys = set(tensors)
        anchor = "embeddings.word_embeddings.weight"
        prefixes = [key.removesuffix(anchor) for key in self.keys if key.endswith(anchor)]
        if len(prefixes) != 1:
            raise EmbeddingError(f"{path.name} has no single XLM-RoBERTa word embedding table")
        self.prefix = prefixes[0]

    def __call__(self, name: str) -> Any:
        return self._get(self.prefix + name)

    def has(self, name: str) -> bool:
        return self.prefix + name in self.keys


def cache_directory() -> Path:
    """The Hugging Face hub cache, as huggingface_hub finds it."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    home = os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME") or "~/.cache", "huggingface")
    return Path(home).expanduser() / "hub"


def cached_models() -> list[str]:
    """The models in the local Hugging Face cache that kgdistiller can run, read from the cache's files alone:
    an XLM-RoBERTa configuration, sentencepiece.bpe.model, a pooling configuration and weights."""
    found: list[str] = []
    root = cache_directory()
    if not root.is_dir():
        return found
    for repository in sorted(root.glob("models--*")):
        names: dict[str, Path] = {}
        for snapshot in sorted((repository / "snapshots").glob("*")):
            for path in snapshot.rglob("*"):
                if path.is_file():
                    names.setdefault(path.relative_to(snapshot).as_posix(), path)
        if not all(name in names for name in CONFIG_FILES) or not any(name in names for name in WEIGHT_FILES):
            continue
        try:
            if json.loads(names["config.json"].read_text(encoding="utf-8")).get("model_type") != "xlm-roberta":
                continue
        except (OSError, ValueError):
            continue
        found.append(repository.name.removeprefix("models--").replace("--", "/"))
    return found


@dataclass(frozen=True)
class Description:
    """What a model's files say about it, checked; nothing is loaded."""

    model: str
    files: dict[str, Path | None]
    weights: Path
    cls: bool
    prompts: dict[str, str]
    limit: int | None

    def report(self) -> dict[str, Any]:
        return {"model": self.model, "pooling": "cls" if self.cls else "mean", "weights": self.weights.name,
                "precision": PRECISION}


def describe(model: str) -> Description:
    """Fetch a model's configuration, tokenizer and weights into the Hugging Face cache and check that kgdistiller can run it.

    Network access follows ``HF_HUB_OFFLINE``; with it set, only the cache is read.
    """
    # huggingface_hub reads this when first imported, which happens only after this point:
    # in ``_download`` and, for ``Encoder``, with transformers after this function returns.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    if not all(importlib.util.find_spec(name) for name in ("numpy", "torch", "transformers", "sentencepiece")):
        raise RetrievalExtraMissing("install kgdistiller[retrieval]")

    def fetch(name: str, required: bool = True) -> Path | None:
        try:
            return _download(model, name, required)
        except Exception as error:
            raise EmbeddingError(f"cannot load embedding model {model}: {error}") from error

    # The configuration first, so that a model kgdistiller cannot run is refused before its weights download.
    files: dict[str, Path | None] = {"config.json": fetch("config.json")}
    settings = json.loads(files["config.json"].read_text(encoding="utf-8"))
    if settings.get("model_type") != "xlm-roberta":
        raise EmbeddingError(
            f"{model} is a {settings.get('model_type')} model; kgdistiller embeds with XLM-RoBERTa "
            "sentence encoders that ship sentencepiece.bpe.model, such as BAAI/bge-m3"
        )
    files.update({name: fetch(name) for name in CONFIG_FILES[1:]})
    files.update({name: fetch(name, required=False) for name in OPTIONAL_FILES})
    pooling = json.loads(files["1_Pooling/config.json"].read_text(encoding="utf-8"))
    modes = [mode for mode in ("cls_token", "mean_tokens") if pooling.get(f"pooling_mode_{mode}")]
    others = [key for key, value in pooling.items() if key.startswith("pooling_mode_") and value
              and key not in ("pooling_mode_cls_token", "pooling_mode_mean_tokens")]
    if len(modes) != 1 or others:
        raise EmbeddingError(f"{model} pools with {pooling}; kgdistiller supports CLS or mean pooling")
    weights = next((path for name in WEIGHT_FILES if (path := fetch(name, required=False))), None)
    if weights is None:
        raise EmbeddingError(f"cannot load embedding model {model}: it has no {' or '.join(WEIGHT_FILES)}")
    sentence = files["config_sentence_transformers.json"]
    prompts: dict[str, str] = (json.loads(sentence.read_text(encoding="utf-8")).get("prompts") or {}) if sentence else {}
    bert = files["sentence_bert_config.json"]
    limit = json.loads(bert.read_text(encoding="utf-8")).get("max_seq_length") if bert else None
    return Description(model, files, weights, modes[0] == "cls_token", prompts, limit)


class Encoder:
    """One loaded model, addressed by its id."""

    def __init__(self, model: str) -> None:
        described = describe(model)
        try:
            import numpy
            import sentencepiece
            import torch
            from transformers import XLMRobertaConfig, XLMRobertaModel
        except ImportError as error:
            raise RetrievalExtraMissing("install kgdistiller[retrieval]") from error
        files, weights, prompts, limit = described.files, described.weights, described.prompts, described.limit
        config = XLMRobertaConfig.from_pretrained(files["config.json"].parent)

        self.model = model
        self.max_tokens: int = min(limit or config.max_position_embeddings - 2, config.max_position_embeddings - 2)
        self._numpy = numpy
        self._torch = torch
        self._cls = described.cls
        self._document_prompt = next((prompts[name] for name in DOCUMENT_PROMPTS if name in prompts), "")
        self._query_prompt = prompts.get(QUERY_PROMPT, "")
        self._pieces = sentencepiece.SentencePieceProcessor(model_file=str(files["sentencepiece.bpe.model"]))
        self._device = torch.device(
            "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        )
        self._dtype = torch.float32 if self._device.type == "cpu" else torch.float16
        try:
            self._network = self._build(XLMRobertaModel, config, _Weights(weights))
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingError(f"cannot load embedding model {model}: {error}") from error

    def _build(self, network_class: Any, config: Any, weights: _Weights) -> Any:
        """The network on the meta device, every Linear and Embedding replaced by its int8 form, then the rest loaded."""
        torch = self._torch
        with torch.device("meta"):
            network = network_class(config, add_pooling_layer=False)

        def replace(module: Any, path: str) -> None:
            for name, child in list(module.named_children()):
                key = f"{path}{name}"
                if isinstance(child, torch.nn.Linear):
                    bias = f"{key}.bias"
                    setattr(module, name, _Int8Linear(self, weights(f"{key}.weight"),
                                                      weights(bias) if weights.has(bias) else None))
                elif isinstance(child, torch.nn.Embedding):
                    setattr(module, name, _Int8Embedding(self, weights(f"{key}.weight")))
                else:
                    replace(child, f"{key}.")

        replace(network, "")
        rest = [name for name, parameter in network.named_parameters() if parameter.is_meta]
        network.load_state_dict({name: weights(name).to(self._dtype) for name in rest}, strict=False, assign=True)
        embeddings = network.embeddings
        embeddings.position_ids = torch.arange(config.max_position_embeddings).expand((1, -1))
        embeddings.token_type_ids = torch.zeros(embeddings.position_ids.size(), dtype=torch.long)
        return network.to(self._device).eval()

    def quantize(self, weight: Any, device: Any) -> tuple[Any, Any]:
        """Symmetric int8 rows of ``weight`` and their scales, computed in chunks of rows."""
        torch = self._torch
        rows = weight.shape[0]
        values = torch.empty(weight.shape, dtype=torch.int8, device=device)
        scales = torch.empty((rows, 1), dtype=torch.float32, device=device)
        for start in range(0, rows, CHUNK_ROWS):
            chunk = weight[start:start + CHUNK_ROWS].float()
            scale = chunk.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127
            values[start:start + CHUNK_ROWS] = (chunk / scale).round_().clamp_(-127, 127).to(torch.int8).to(device)
            scales[start:start + CHUNK_ROWS] = scale.to(device)
        return values, scales.to(self._dtype)

    def _ids(self, prompt: str, text: str) -> list[int]:
        pieces = [piece + OFFSET if piece else UNK for piece in self._pieces.encode(prompt + text)]
        return [BOS, *pieces, EOS]

    def over_limit(self, texts: list[str]) -> list[int]:
        """Positions of the texts whose document-prompted token count exceeds the model's limit."""
        return [position for position, text in enumerate(texts)
                if len(self._ids(self._document_prompt, text)) > self.max_tokens]

    def _rows(self, prompt: str, texts: list[str]) -> list[bytes]:
        torch = self._torch
        rows: list[bytes] = []
        try:
            with torch.inference_mode():
                for text in texts:
                    ids = self._ids(prompt, text)
                    if len(ids) > self.max_tokens:
                        ids = [*ids[:self.max_tokens - 1], EOS]
                    hidden = self._network(input_ids=torch.tensor([ids], device=self._device)).last_hidden_state[0]
                    pooled = hidden[0] if self._cls else hidden.mean(0)
                    vector = torch.nn.functional.normalize(pooled.float(), dim=-1).cpu()
                    rows.append(self._numpy.asarray(vector.numpy(), dtype="<f4").tobytes())
        except Exception as error:
            raise EmbeddingError(f"embedding with {self.model} failed: {error}") from error
        finally:
            # Give the activations' memory back between requests.
            if self._device.type == "mps":
                torch.mps.empty_cache()
            elif self._device.type == "cuda":
                torch.cuda.empty_cache()
        return rows

    def encode_documents(self, texts: list[str]) -> list[bytes]:
        return self._rows(self._document_prompt, texts)

    def encode_query(self, text: str) -> bytes:
        return self._rows(self._query_prompt, [text])[0]


@cache
def _int8_modules() -> tuple[type, type]:
    import torch

    class Int8Linear(torch.nn.Module):
        """A Linear layer stored as int8 rows and their scales, dequantized per call."""

        def __init__(self, encoder: Encoder, weight: Any, bias: Any) -> None:
            super().__init__()
            self.values, self.scales = encoder.quantize(weight, encoder._device)
            self.bias = None if bias is None else bias.to(encoder._dtype).to(encoder._device)

        def forward(self, inputs: Any) -> Any:
            return torch.nn.functional.linear(inputs, self.values.to(inputs.dtype) * self.scales, self.bias)

    class Int8Embedding(torch.nn.Module):
        """An embedding table kept as int8 rows in CPU memory; looked-up rows move to the model's device."""

        def __init__(self, encoder: Encoder, weight: Any) -> None:
            super().__init__()
            self.values, self.scales = encoder.quantize(weight, "cpu")
            self.dtype = encoder._dtype

        def forward(self, ids: Any) -> Any:
            rows = ids.cpu()
            return (self.values[rows].to(self.dtype) * self.scales[rows]).to(ids.device)

    return Int8Linear, Int8Embedding


def _Int8Linear(encoder: Encoder, weight: Any, bias: Any) -> Any:
    return _int8_modules()[0](encoder, weight, bias)


def _Int8Embedding(encoder: Encoder, weight: Any) -> Any:
    return _int8_modules()[1](encoder, weight)


def encoder(model: str) -> Any:
    """A client of the user's one embedding process for ``model``; nothing loads in this process."""
    from .embed_service import Client

    return Client(model)
