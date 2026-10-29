"""Official checkpoint loading and per-path inference, isolated from the UI."""
from pathlib import Path
import threading
from .core import MODEL_ALIASES

UPSTREAM_REVISION = '67b630e67f6a18c9e9be918d9b4337c960db1e9a'
_models = {}
_lock = threading.Lock()


def get_predictor(name='small', progress=None):
    if name not in MODEL_ALIASES:
        raise ValueError('Unsupported Kronos model')
    with _lock:
        if name in _models:
            return _models[name]
        import torch
        from huggingface_hub import snapshot_download
        from kronos_model import Kronos, KronosTokenizer, KronosPredictor
        model_id, tokenizer_id = MODEL_ALIASES[name]
        local_cache = Path(__file__).resolve().parent.parent/'.cache/kronos_research/models'

        def checkpoint(repo):
            for cache in (None, str(local_cache)):
                try:
                    path = Path(snapshot_download(repo, cache_dir=cache, local_files_only=True))
                    if (path/'config.json').exists() and (path/'model.safetensors').exists():
                        return path
                except (OSError, ValueError):
                    pass
            if progress:
                progress(0, f'Downloading {repo.split("/")[-1]} for first use')
            return Path(snapshot_download(repo, cache_dir=str(local_cache),
                        allow_patterns=['config.json', 'model.safetensors']))

        model_path, tokenizer_path = checkpoint(model_id), checkpoint(tokenizer_id)
        device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        if progress:
            progress(0, f'Loading Kronos-{name} on {device}')
        tokenizer = KronosTokenizer.from_pretrained(str(tokenizer_path)).eval()
        network = Kronos.from_pretrained(str(model_path)).eval()
        predictor = KronosPredictor(network, tokenizer, device=device,
                                    max_context=2048 if name == 'mini' else 512)
        predictor.research_metadata = {'model': model_id, 'tokenizer': tokenizer_id,
            'model_revision': model_path.name, 'tokenizer_revision': tokenizer_path.name,
            'device': device, 'torch_version': torch.__version__, 'upstream_revision': UPSTREAM_REVISION}
        _models[name] = predictor
        return predictor
