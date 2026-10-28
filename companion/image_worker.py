"""Short-lived MFLUX process. Called with argv, never through a shell."""
from __future__ import annotations

import argparse
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="flux2-klein-4b")
    parser.add_argument("--model-path")
    parser.add_argument("--quantize", type=int, default=4)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--ready-file", help="optional local benchmark signal after model load")
    args = parser.parse_args()
    # Import inside the worker so the bot never holds MLX/model memory.
    from mflux.models.common.config import ModelConfig
    from mflux.callbacks.instances.memory_saver import MemorySaver

    if args.model == "z-image-turbo":
        from mflux.models.z_image import ZImage
        model = ZImage(model_config=ModelConfig.z_image_turbo(), quantize=args.quantize, model_path=args.model_path)
    else:
        from mflux.models.flux2.variants import Flux2Klein
        config = ModelConfig.from_name(args.model)
        model = Flux2Klein(model_config=config, quantize=args.quantize, model_path=args.model_path)
    model.callbacks.register(MemorySaver(model=model, keep_transformer=False))
    if args.ready_file:
        Path(args.ready_file).write_text("loaded")
    result = model.generate_image(seed=secrets.randbelow(2**31), prompt=args.prompt,
                                  num_inference_steps=args.steps, width=args.width, height=args.height)
    result.save(args.output)


if __name__ == "__main__":
    main()
