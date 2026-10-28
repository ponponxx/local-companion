"""Isolated, offline-only embedding process; stdin/stdout JSON lines."""
import argparse
import contextlib
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-path', required=True)
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(2)
        model = SentenceTransformer(
            args.model_path, device='cpu', local_files_only=True, trust_remote_code=False,
            config_kwargs={'vision_config': None, 'audio_config': None},
            model_kwargs={'torch_dtype': torch.float32},
        )
        model.max_seq_length = 512
    for line in sys.stdin:
        try:
            request = json.loads(line)
            texts = request['texts']
            if not isinstance(texts, list) or not 1 <= len(texts) <= 64 or any(not isinstance(t, str) or len(t) > 4000 for t in texts):
                raise ValueError('invalid input')
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                prompt_name = request.get('prompt_name', 'Classification')
                if prompt_name not in {'Classification', 'Retrieval-query', 'Retrieval-document'}:
                    raise ValueError('invalid prompt')
                vectors = model.encode(texts, prompt_name=prompt_name, normalize_embeddings=True,
                                       batch_size=8, show_progress_bar=False).tolist()
            response = {'vectors': vectors}
        except Exception as exc:
            response = {'error': type(exc).__name__}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
